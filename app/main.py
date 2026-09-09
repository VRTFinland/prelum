import uuid
from collections.abc import Awaitable, Callable
from typing import cast, override

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message
from structlog.typing import FilteringBoundLogger

from app.api.routes import router
from app.core.errors import (
    AppError,
    InvalidFileDataError,
    InvalidRequestError,
    InvalidTemplatePathError,
    RequestTooLargeError,
    UnsupportedFormatError,
    app_error_handler,
)
from app.core.logging import bind_request_context, clear_request_context, setup_logging
from app.core.sentry import setup_sentry
from app.deps import get_settings


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """
    Reject an oversized request body while it is still arriving.

    Counts the chunks actually received rather than trusting Content-Length, and raises as soon
    as the total passes max_body — so a 20 MB inline template is refused mid-stream instead of
    being buffered whole and then rejected.
    """

    max_body: int

    def __init__(self, app: ASGIApp, max_body: int) -> None:
        super().__init__(app)
        self.max_body = max_body

    @override
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        received = 0
        exceeded = False
        original_receive = request.receive

        async def receive_with_limit() -> Message:
            nonlocal received, exceeded
            message = await original_receive()
            if message["type"] != "http.request":
                return message

            body = message.get("body", b"") or b""
            received += len(body)
            if received > self.max_body:
                exceeded = True
                raise RequestTooLargeError(f"Request body exceeds {self.max_body} bytes")

            return message

        request._receive = receive_with_limit
        try:
            response = await call_next(request)
        except RequestTooLargeError as exc:
            return app_error_handler(request, exc)
        if exceeded:
            return app_error_handler(
                request,
                RequestTooLargeError(f"Request body exceeds {self.max_body} bytes"),
            )
        return response


def create_app() -> FastAPI:
    settings = get_settings()

    # Initialise Sentry before other setup
    setup_sentry(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )

    setup_logging()

    startup_logger: FilteringBoundLogger = cast(FilteringBoundLogger, structlog.get_logger())
    if settings.is_development:
        startup_logger.warning(
            "startup.using_dev_token",
            message="Using the published development token. Set PRELUM_API_TOKEN for any real deployment.",
            environment=settings.effective_environment,
        )

    app: FastAPI = FastAPI(title="Prelum", version="1.0.0")
    app.include_router(router)
    app.add_middleware(BodyLimitMiddleware, max_body=settings.max_request_body_bytes)

    Instrumentator().instrument(app).expose(app)

    @app.middleware("http")
    async def add_request_id(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        bind_request_context(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            clear_request_context()
        response.headers["x-request-id"] = request_id
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return app_error_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Dropping "input" keeps the caller's own template source and assets — which is what it
        # mirrors — out of responses, proxies and logs. loc and msg identify the problem without it.
        errors = cast(list[dict[str, object]], exc.errors())
        detail = [{k: v for k, v in error.items() if k != "input"} for error in errors]
        classified_errors: dict[str, type[AppError]] = {
            InvalidTemplatePathError.code: InvalidTemplatePathError,
            InvalidFileDataError.code: InvalidFileDataError,
        }
        error_class = next(
            (
                classified
                for error in errors
                if (classified := classified_errors.get(cast(str, error.get("type")))) is not None
            ),
            InvalidRequestError,
        )
        if error_class is InvalidRequestError and any(
            (error.get("type") == "enum" and cast(tuple[object, ...], error["loc"])[-2:] == ("output", "format"))
            or (
                error.get("type") == "union_tag_invalid"
                and cast(tuple[object, ...], error["loc"])[-1:] == ("output",)
                and cast(dict[str, object], error.get("ctx", {})).get("discriminator") == "'format'"
            )
            for error in errors
        ):
            error_class = UnsupportedFormatError
        # Validation errors carry the raised exception in ctx; encode those at any depth so the
        # response body cannot fail to serialise.
        error = error_class(detail=jsonable_encoder(detail, custom_encoder={Exception: str}))
        return error.to_response(request)

    return app


app = create_app()
