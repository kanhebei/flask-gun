from __future__ import annotations

from typing import Any, Callable, Optional

from flask import Flask

from .constants import NOT_SET
from .model_field import ModelField
from .operation import Callback, Operation


class Router:
    def __init__(
            self,
            auth: Any = NOT_SET,
            app: Optional[Flask] = None,
            operations: Optional[list[Operation]] = None,
    ):
        self.app = app
        self.auth = auth
        self.operations: list[Operation] = operations or []

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
        return self.add_route(
            "GET",
            path,
            responses,
            auth=auth,
            summary=summary,
            description=description,
            params=params,
            callbacks=callbacks
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
        return self.add_route(
            "POST",
            path,
            responses,
            auth=auth,
            summary=summary,
            description=description,
            params=params,
            callbacks=callbacks
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
        return self.add_route(
            "PUT",
            path,
            responses,
            auth=auth,
            summary=summary,
            description=description,
            params=params,
            callbacks=callbacks
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
        return self.add_route(
            "PATCH",
            path,
            responses,
            auth=auth,
            summary=summary,
            description=description,
            params=params,
            callbacks=callbacks
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
        return self.add_route(
            "DELETE",
            path,
            responses,
            auth=auth,
            summary=summary,
            description=description,
            params=params,
            callbacks=callbacks
        )

    def add_route(
            self,
            method: str,
            path: str,
            responses: Optional[Any] = None,
            auth: Any = NOT_SET,
            summary: str = "",
            description: str = "",
            params: Optional[list[ModelField]] = None,
            callbacks: Optional[list[Callback]] = None,
    ) -> Callable:
        def decorator(func: Callable) -> Callable:
            operation = Operation(
                path,
                method,
                func,
                responses,
                auth=auth if auth != NOT_SET else self.auth,
                summary=summary,
                description=description,
                params=params,
                callbacks=callbacks,
            )
            self.add_operation(operation)
            return func

        return decorator

    def add_router(self, router: Router, prefix: str = "") -> None:
        for operation in router.operations:
            operation.add_prefix(prefix)
            operation.update_auth(self.auth)
            self.add_operation(operation)

    def add_operation(self, operation: Operation) -> None:
        self.operations.append(operation)
        if self.app:
            self.app.add_url_rule(
                operation.path,
                operation.view_func.__name__,
                operation.run,
                methods=[operation.method],
            )
