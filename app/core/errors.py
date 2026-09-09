from collections.abc import Callable
from http import HTTPStatus
from typing import cast, override

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from structlog.typing import FilteringBoundLogger

# Keys and filenames come from the caller and are unbounded; quoting them whole would copy request
# content into every error response and log line.
_MESSAGE_VALUE_LIMIT = 80


def for_message(value: str) -> str:
    """Shorten a caller-supplied value for inclusion in an error message."""
    if len(value) <= _MESSAGE_VALUE_LIMIT:
        return value
    return f"{value[:_MESSAGE_VALUE_LIMIT]}…"


def status_is_server_fault(status: HTTPStatus) -> bool:
    """Whether a failure with this status is ours rather than the caller's, i.e. worth paging for."""
    return status >= HTTPStatus.INTERNAL_SERVER_ERROR


class AppError(Exception):
    status: HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR
    title: str = "Internal Server Error"

    # A stable identifier for this failure, and the thing callers are meant to branch on. Status
    # cannot serve that purpose: four different errors answer 413, and a caller telling "your image
    # is too large" from "the rendered document is too large" has nothing else to key on. `title` is
    # prose for humans and may be reworded; `code` is a published contract and may not.
    code: str = "internal_error"

    detail: object

    def __init__(self, message: str | None = None, *, detail: object | None = None) -> None:
        super().__init__(message or self.title)
        self.detail = detail or message or self.title

    @property
    def is_server_fault(self) -> bool:
        return status_is_server_fault(self.status)

    def to_response(self, request: Request) -> JSONResponse:
        body: dict[str, object] = {
            "code": self.code,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": str(request.url),
        }
        return JSONResponse(
            status_code=self.status.value,
            content=body,
            media_type="application/problem+json",
        )


class InvalidRequestError(AppError):
    status: HTTPStatus = HTTPStatus.BAD_REQUEST
    title: str = "Invalid Request"
    code: str = "invalid_request"


class UnsupportedFormatError(AppError):
    status: HTTPStatus = HTTPStatus.BAD_REQUEST
    title: str = "Unsupported Format"
    code: str = "unsupported_format"


class InvalidTemplatePathError(AppError):
    status: HTTPStatus = HTTPStatus.BAD_REQUEST
    title: str = "Invalid Template Path"
    code: str = "invalid_template_path"


class RenderError(AppError):
    status: HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR
    title: str = "Render Failed"
    code: str = "render_failed"


class InlineTemplateError(AppError):
    """A caller-supplied inline template failed to compile — their input, not our fault."""

    status: HTTPStatus = HTTPStatus.UNPROCESSABLE_ENTITY
    title: str = "Inline Template Failed"
    code: str = "template_compile_failed"


class ServiceUnavailableError(AppError):
    status: HTTPStatus = HTTPStatus.SERVICE_UNAVAILABLE
    title: str = "Service Unavailable"
    code: str = "service_unavailable"


class ServiceOverloadedError(AppError):
    """Raised when no render slot became free within the configured queue wait.

    429 rather than 503 on purpose: `status_is_server_fault` treats everything from 500 up as an
    incident, and shedding load is designed behaviour, not a fault. A busy service should not page
    anyone. The Retry-After header travels with the response so the caller knows when to return.
    """

    status: HTTPStatus = HTTPStatus.TOO_MANY_REQUESTS
    title: str = "Too Many Requests"
    code: str = "render_queue_full"

    retry_after: int

    def __init__(self, *, retry_after: int) -> None:
        super().__init__("Render queue is full; retry shortly")
        self.retry_after = retry_after

    @override
    def to_response(self, request: Request) -> JSONResponse:
        response = super().to_response(request)
        response.headers["Retry-After"] = str(self.retry_after)
        return response


class ForbiddenError(AppError):
    status: HTTPStatus = HTTPStatus.FORBIDDEN
    title: str = "Forbidden"
    code: str = "forbidden"


class StringTooLargeError(AppError):
    status: HTTPStatus = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title: str = "String Too Large"
    code: str = "string_too_large"

    def __init__(self, *, size: int, max_allowed: int) -> None:
        super().__init__(f"String size {size} bytes exceeds limit {max_allowed}")


class InvalidFileDataError(AppError):
    status: HTTPStatus = HTTPStatus.BAD_REQUEST
    title: str = "Invalid File Data"
    code: str = "invalid_file_data"


class RequestTooLargeError(AppError):
    status: HTTPStatus = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title: str = "Request Too Large"
    code: str = "request_too_large"


class RenderTimeoutError(AppError):
    status: HTTPStatus = HTTPStatus.REQUEST_TIMEOUT
    title: str = "Render Timeout"
    code: str = "render_timeout"


class OutputTooLargeError(AppError):
    status: HTTPStatus = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title: str = "Output Too Large"
    code: str = "output_too_large"

    def __init__(self, *, size: int, max_allowed: int) -> None:
        super().__init__(f"Output size {size} bytes exceeds limit {max_allowed}")


class TooManyOutputFilesError(AppError):
    status: HTTPStatus = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title: str = "Too Many Output Files"
    code: str = "too_many_output_files"

    def __init__(self, *, count: int, max_allowed: int) -> None:
        super().__init__(f"Output file count {count} exceeds limit {max_allowed}")


class TemplateTooLargeError(AppError):
    status: HTTPStatus = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title: str = "Template Too Large"
    code: str = "template_too_large"

    def __init__(self, *, size: int, max_allowed: int, name: str | None = None) -> None:
        subject = f"Template file '{name}'" if name else "Template source"
        super().__init__(f"{subject} size {size} bytes exceeds limit {max_allowed}")


def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger = cast(FilteringBoundLogger, structlog.get_logger())
    log_method: Callable[..., None] = logger.error if exc.is_server_fault else logger.warning

    log_method(
        "app.error",
        status=exc.status.value,
        title=exc.title,
        detail=exc.detail,
        path=str(request.url),
        method=request.method,
    )

    return exc.to_response(request)
