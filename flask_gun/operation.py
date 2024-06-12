# pylint:disable=comparison-with-callable
import inspect
import re
from collections import defaultdict
from enum import Enum
from typing import Any, Callable, Dict, Optional, Type, Union, cast, get_origin

from docstring_parser import parse as doc_parse
from flask import jsonify, request
from werkzeug.exceptions import Unauthorized
from pydantic import BaseModel, ConfigDict, ValidationError

from .constants import NOT_SET, ApiConfigError, ParamType
from .model_field import FieldMapping, ModelField, Undefined
from .models import MediaType
from .models import Operation as OAPIOperation
from .models import (
    Parameter,
    ParameterInType,
    PathItem,
    Reference,
    RequestBody,
    Response,
    Schema,
)
from .param import FuncParam
from .parse_rule import parse_rule
from .security import HttpAuthBase
from .utils import analyze_param, create_model_field, is_scalar_sequence_field

ModelNameMapType = dict[Union[Type[BaseModel], Type[Enum]], str]


class SerializationModel(BaseModel):
    data: Any

    model_config = ConfigDict(arbitrary_types_allowed=True)


class Callback(BaseModel):
    name: str
    url: str
    method: str
    request_body: Optional[Type] = None
    params: Optional[list[ModelField]] = None
    response_codes: dict[int, str]
    field: Optional[ModelField] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def model_post_init(self, __context: Any) -> None:
        self.field = (
            create_model_field(self.name, self.request_body)
            if self.request_body
            else None
        )


def is_sqlalchemy_model(obj):
    return hasattr(obj, '_sa_instance_state')


def is_orm(obj):
    return is_sqlalchemy_model(obj)


class Operation:
    """Operation represents handler for one endpoint."""

    def __init__(
            self,
            path: str,
            method: str,
            view_func: Callable,
            responses: Optional[Dict[int, BaseModel]] = None,
            callbacks: Optional[list[Callback]] = None,
            summary: str = "",
            description: str = "",
            params: Optional[list[ModelField]] = None,
            auth: Any = NOT_SET,
    ):
        self.path = path
        self.method = method
        self.view_func = view_func
        self.definitions: dict = {}
        self.responses = self._sanitize_responses(responses, self.view_func)
        self.callbacks = callbacks
        self.summary = summary
        self.description = description
        self.auth = auth
        self.params = params or self._parse_params(path)

    def run(self, *args: Any, **kwargs: Any) -> Any:
        # Run authentication if configured
        if self.auth and self.auth() is None:
            # raise Unauthorized(description='权限不足')
            return jsonify("Unauthorized"), 401

        try:
            for param in self.params:
                # Parse query params
                field_info = cast(FuncParam, param.field_info)
                if field_info.in_ == ParamType.QUERY and param.name in request.args:
                    if is_scalar_sequence_field(param):
                        kwargs[param.name] = param.type_adapter.validate_python(
                            request.args.getlist(param.alias)
                        )
                    else:
                        kwargs[param.name] = param.type_adapter.validate_python(
                            request.args[param.alias]
                        )
                elif field_info.in_ == ParamType.HEADER:
                    kwargs[param.name] = param.type_adapter.validate_python(
                        request.headers.get(param.alias)
                    )
                # Parse request body
                elif field_info.in_ == ParamType.BODY:
                    kwargs[param.name] = param.type_adapter.validate_python(
                        request.json
                    )
        except ValidationError as validation_error:
            return validation_error.json(), 400

        def _resp_json(obj, status_code=200, header=None):
            if status_code in self.responses.keys():
                if isinstance(obj, dict):
                    return (
                        self.responses[status_code].type_(**obj).model_dump(),
                        status_code,
                        header
                    )
                if is_orm(obj):
                    return (
                        self.responses[status_code].type_.model_validate(obj).model_dump(),
                        status_code,
                        header
                    )

            return obj, status_code, header

        # Run the original view function
        rv = self.view_func(*args, **kwargs)

        if isinstance(rv, Response):
            return rv

        if not isinstance(rv, tuple):
            return _resp_json(rv, 200)

        if len(rv) == 2:
            return _resp_json(rv[0], rv[1]) if isinstance(rv[1], int) else _resp_json(rv[0], 200, rv[1])

        elif len(rv) >= 3:
            # 保留前三个元素（JSON，状态码，响应头）
            return _resp_json(rv[0], rv[1], rv[2])

        else:
            return _resp_json(rv[0])

    @staticmethod
    def _sanitize_responses(
            responses: Any, view_func: Callable
    ) -> dict[int, ModelField]:
        func_return_type = view_func.__annotations__.get("return")

        # Return code not specified, setting it to 200
        if not isinstance(responses, dict):
            responses = (
                {200: create_model_field("Response 200", responses)}
                if responses
                else {}
            )
        else:
            # convert all response codes to ints
            responses = {
                int(k): create_model_field(f"Response {k}", v)
                for k, v in responses.items()
            }

        # If responses weren't specified, try to generate it from return type
        if not responses:
            # It can't be an Union
            if func_return_type is None or get_origin(func_return_type) == Union:
                raise ApiConfigError("Return type not specified.")
            responses[200] = create_model_field("Response 200", func_return_type)

        # Check if for each returned type there is implicitly or explicitly defined response model and code
        if func_return_type:
            if get_origin(func_return_type) == Union:
                for ret_type in func_return_type.__args__:
                    if not any(resp.type_ != ret_type for resp in responses.values()):
                        raise ApiConfigError(
                            f"Return type {ret_type} http code must be specified explicitly."
                        )

            # If we specified different return type as we specified as response
            elif (
                    200 in responses
                    and responses[200].field_info.annotation != func_return_type
            ):
                raise ApiConfigError(
                    f"Return type of the function {type(func_return_type)} does not match response type {type(responses[200].type_)}"
                )
        return responses

    @classmethod
    def serialize(cls, resp: Any) -> Any:
        """Convert response object into json serializable object."""
        return SerializationModel(data=resp).model_dump(mode="json")["data"]

    def get_callback_schema(
            self, cb: Callback, field_mapping: FieldMapping
    ) -> dict[str, PathItem]:
        """Generate schema for a callback.

        Currently, a lot of code is duplicated with endpoints schema.
        In the near future, I plan to unify it. It will also make easier
        to declare callbacks.
        """

        if cb.field:
            request_body = field_mapping[(cb.field, "validation")]
        else:
            request_body = None

        parameters: list[Union[Parameter, Reference]] = []
        for param in cb.params or []:
            field_info = param.field_info
            field_info = cast(FuncParam, field_info)
            if not field_info.include_in_schema:
                continue
            if field_info.in_ == ParamType.BODY:
                continue
            parameter = Parameter(
                name=param.alias,
                in_=ParameterInType(field_info.in_.value),
                # Undefined type is tricky, because it can't be serialized
                required=param.required,
                schema_=Schema.model_validate(field_mapping[(param, "validation")]),
                description=field_info.description,
                examples=field_info.examples,
                example=field_info.example if field_info.example != Undefined else None,
                deprecated=field_info.deprecated,
            )
            parameters.append(parameter)

        schema = OAPIOperation(
            requestBody=RequestBody(
                content={
                    "application/json": MediaType(schema_=request_body)  # type:ignore
                },
                required=True,
            )
            if request_body
            else None,
            parameters=parameters or None,
            responses={
                str(code): Response(description=description)
                for code, description in cb.response_codes.items()
            },
        )

        return {cb.url: PathItem.model_validate({cb.method.lower(): schema})}

    def get_openapi_parameters(self, field_mapping: FieldMapping) -> list[Parameter]:
        """Create OpenAPI schema for parameters of this operation."""
        parameters = []
        for param in self.params:
            field_info = cast(FuncParam, param.field_info)

            if not field_info.include_in_schema:
                continue
            if field_info.in_ == ParamType.BODY:
                continue
            parameter = Parameter(
                name=param.alias,
                in_=ParameterInType(field_info.in_.value),
                required=param.required,
                schema_=Schema.model_validate(field_mapping[(param, "validation")]),
                description=field_info.description,
                examples=field_info.examples,
                example=field_info.example if field_info.example != Undefined else None,
                deprecated=field_info.deprecated,
            )
            parameters.append(Parameter.model_validate(parameter))

        return parameters

    def get_openapi_request_body(
            self, field_mapping: FieldMapping
    ) -> Optional[RequestBody]:
        """Create OpenAPI schema for request body of this operation.

        Note: There can be at most one request body.
        """
        for param in self.params:
            field_info = cast(FuncParam, param.field_info)
            if field_info.in_ == ParamType.BODY:
                request_body = field_mapping[(param, "validation")]
                return RequestBody(
                    content={
                        "application/json": MediaType(
                            schema_=Schema.model_validate(request_body),
                        )
                    },
                    description="",
                    required=True,
                )
        return None

    def get_schema(self, field_mapping: FieldMapping) -> OAPIOperation:
        """Create OpenAPI schema for this operation."""
        doc = doc_parse(self.view_func.__doc__ or "")
        responses: Dict[str, Response] = {}

        for code, response in self.responses.items():
            response_schema = field_mapping[(response, "validation")]
            responses[str(code)] = Response(
                content={
                    "application/json": MediaType(
                        schema_=Schema.model_validate(response_schema)
                    )
                },
                description="",
            )

        callbacks = {
            cb.name: self.get_callback_schema(cb, field_mapping=field_mapping)
            for cb in (self.callbacks or [])
        }

        return OAPIOperation(
            summary=doc.short_description or self.summary,
            description=doc.long_description or self.description,
            responses=responses,
            parameters=self.get_openapi_parameters(field_mapping=field_mapping)
                       or None,  # type:ignore
            requestBody=self.get_openapi_request_body(field_mapping=field_mapping),
            security=[{self.auth.schema_name: []}] if self.auth else None,
            callbacks=callbacks or None,
        )

    def _parse_path_params(self, path: str) -> list[str]:
        """Extract names of path parameters of the operation."""
        return re.findall(r"(\w+)>", path)

    def _parse_params(self, path: str) -> list[ModelField]:
        """Parse parameters of this operation.

        We take all arguments of the operation function,
        and for each of them determine a location from where it should be taken
        e.g. path, query, request body until it's not set explicitly.
        """
        path_param_names = self._parse_path_params(path)

        param_docs = {
            param.arg_name: param.description or ""
            for param in doc_parse(self.view_func.__doc__ or "").params
        }

        fields = []

        # Additional attributes for a parameter are set via the default value
        # we retrieve the default value using inspect, and we convert it
        # to a ModelField get_param_model_field function
        for param_name, param in inspect.signature(self.view_func).parameters.items():
            model_field = analyze_param(
                param_name=param_name,
                annotation=param.annotation,
                value=param.default,
                is_path_param=param.name in path_param_names,
            )
            if param.name in param_docs and not model_field.field_info.description:
                model_field.field_info.description = param_docs[param.name]

            fields.append(model_field)

        # After the parameters are parsed, we do some checks to ensure consistency of the API
        field_types = defaultdict(list)
        for field in fields:
            field_types[field.field_info.in_].append(field.name)  # type:ignore

        if len(field_types[ParamType.BODY]) > 1:
            raise ApiConfigError("Multiple request body arguments.")

        for path_param in path_param_names:
            if path_param not in field_types[ParamType.PATH]:
                raise ApiConfigError(f"API handler misses {path_param} argument.")

        return fields

    def get_models(self) -> list[ModelField]:
        """Collects all models used in this operation.

        This is needed to get definitions of all models for OpenAPI.
        """
        return (
                self.params
                + list(self.responses.values())
                + list(cb.field for cb in (self.callbacks or []) if cb.field)
                + list(
            param
            for cb in (self.callbacks or [])
            if cb.request_body
            for param in (cb.params or [])
        )
        )

    def get_openapi_path(self) -> str:
        """Convert flask endpoint path into openapi path."""
        subs = []

        for converter, _, variable in parse_rule(self.path):
            if converter is None:
                subs.append(variable)
                continue
            subs.append(f"{{{variable}}}")
        return "".join(subs)

    def add_prefix(self, prefix: str) -> None:
        self.path = prefix + self.path

    def update_auth(self, auth: Optional[HttpAuthBase] = None) -> None:
        self.auth = self.auth if self.auth != NOT_SET else auth
