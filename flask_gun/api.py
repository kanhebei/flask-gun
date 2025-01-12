import re
from typing import Any, Callable, Optional

from flask import Blueprint, Flask, render_template
from pydantic.json_schema import GenerateJsonSchema

from .constants import NOT_SET
from .models import Components, Info, OpenAPI, Server
from .router import Router
from .swagger_ui import swagger_ui_path

from .model_field import ModelField
from .operation import Callback


class GunAPI:
    OPENAPI_VERSION = "3.1.0"

    def __init__(
            self,
            app: Flask,
            auth: Any = NOT_SET,
            title: str = "",
            description: str = "",
            version: str = "1.0.0",
            servers: Optional[list[Server]] = None,
            prefix: str = "",
            docs_url: str = "/docs",
    ):
        swagger_bp = Blueprint(
            "swagger_ui",
            __name__,
            static_url_path="",
            static_folder=swagger_ui_path,
            template_folder=swagger_ui_path,
        )
        swagger_bp.add_url_rule(
            "/",
            "docs",
            lambda: render_template(
                "index.j2", openapi_spec_url=f"{prefix}{docs_url}/openapi.json"
            ),
        )
        swagger_bp.add_url_rule(
            "/openapi.json", "openapi", self.get_schema, methods=["GET"]
        )
        app.register_blueprint(swagger_bp, url_prefix=f"{prefix}{docs_url}")
        self.router = Router(auth=auth, app=app)
        self.title = title
        self.description = description
        self.version = version
        self.servers = servers
        self.prefix = prefix
        self.model_definitions: dict[str, Any] = {}

    def get(
            self,
            path: str,
            responses: Optional[Any] = None,
            auth: Any = NOT_SET,
            summary: str = "",
            description: str = "",
            params: Optional[list[ModelField]] = None,
            callbacks: Optional[list[Callback]] = None,
    ) -> Callable:
        return self.router.add_route(
            "GET",
            self.prefix + path,
            responses,
            auth,
            summary,
            description,
            params,
            callbacks
        )

    def post(
            self,
            path: str,
            responses: Optional[Any] = None,
            auth: Any = NOT_SET,
            summary: str = "",
            description: str = "",
            params: Optional[list[ModelField]] = None,
            callbacks: Optional[list[Callback]] = None,
    ) -> Callable:
        return self.router.add_route(
            "POST", self.prefix + path,
            responses,
            auth,
            summary,
            description,
            params,
            callbacks
        )

    def put(
            self,
            path: str,
            responses: Optional[Any] = None,
            auth: Any = NOT_SET,
            summary: str = "",
            description: str = "",
            params: Optional[list[ModelField]] = None,
            callbacks: Optional[list[Callback]] = None,
    ) -> Callable:
        return self.router.add_route(
            "PUT",
            self.prefix + path,
            responses,
            auth,
            summary,
            description,
            params,
            callbacks
        )

    def patch(
            self,
            path: str,
            responses: Optional[Any] = None,
            auth: Any = NOT_SET,
            summary: str = "",
            description: str = "",
            params: Optional[list[ModelField]] = None,
            callbacks: Optional[list[Callback]] = None,
    ) -> Callable:
        return self.router.add_route(
            "PATCH",
            self.prefix + path,
            responses,
            auth,
            summary,
            description,
            params,
            callbacks
        )

    def delete(
            self,
            path: str,
            responses: Optional[Any] = None,
            auth: Any = NOT_SET,
            summary: str = "",
            description: str = "",
            params: Optional[list[ModelField]] = None,
            callbacks: Optional[list[Callback]] = None,
    ) -> Callable:
        return self.router.add_route(
            "DELETE",
            self.prefix + path,
            responses,
            auth,
            summary,
            description,
            params,
            callbacks
        )

    def add_router(self, router: Router, prefix: str = "") -> None:
        self.router.add_router(router, f"{self.prefix}{prefix}")

    def get_schema(self) -> dict[str, Any]:
        """Creates OpenAPI schema for the API."""

        # At first we collect all pydantic models used anywhere
        # in the endpoints - parameters, responses, request bodies, callbacks...
        models = []
        for operation in self.router.operations:
            models += operation.get_models()

        schema_generator = GenerateJsonSchema(
            ref_template="#/components/schemas/{model}"
        )

        inputs = [
            (field, field.mode, field.type_adapter.core_schema) for field in models
        ]
        field_mapping, definitions = schema_generator.generate_definitions(
            inputs=inputs
        )

        paths: dict = {}
        security_schemes: dict = {}
        # Create OpenAPI schemas for all models

        # Create OpenAPI schema for all operations
        for operation in self.router.operations:
            # Arguments in paths has format e.g. <int:id> in flask which is different than in openapi e.g. {id}
            # therefore we need to convert it to openapi format
            swagger_path = operation.get_openapi_path()
            if swagger_path not in paths:
                paths[swagger_path] = {}
            paths[swagger_path][operation.method.lower()] = operation.get_schema(
                field_mapping=field_mapping
            )
            if operation.auth:
                security_schemes.update(operation.auth.schema())

        schema = OpenAPI(
            openapi=self.OPENAPI_VERSION,
            info=Info(
                title=self.title,
                description=re.sub(r"\n *", "\n", self.description),
                version=self.version,
            ),
            components=Components(
                schemas=definitions, securitySchemes=security_schemes
            ),
            paths=paths,
            servers=self.servers or None,
        )

        return schema.model_dump(mode="json", by_alias=True, exclude_none=True)
