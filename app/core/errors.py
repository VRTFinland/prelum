from collections.abc import Callable, Iterator, Mapping
from enum import StrEnum
from http import HTTPStatus
from typing import Literal, cast, override

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from structlog.typing import FilteringBoundLogger

# Keys and filenames come from the caller and are unbounded; quoting them whole would copy request
# content into every error response and log line.
_MESSAGE_VALUE_LIMIT = 80


def for_message(value: str, *, limit: int = _MESSAGE_VALUE_LIMIT) -> str:
    """
    Shorten caller text and escape lone surrogates so errors remain encodable as UTF-8.

    :param limit: Raised only where the text quoting the value is itself worth keeping, as with a
        validation message that embeds the offending value inside prose the caller needs to read.
    """
    # Only inspect enough text to decide whether it needs shortening. Escaping before the final
    # bound keeps even surrogate-heavy paths bounded, while preserving ordinary Unicode text.
    value = value[: limit + 1].encode("utf-8", errors="backslashreplace").decode("utf-8")
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…"


class Origin(StrEnum):
    """What a failure is attributable to, published beside `code` on every problem body.

    Status cannot carry this. Seven codes answer 413 and only some are the caller's doing, so a
    caller that alerts on the rest has to keep its own list of codes — and such a list drifts in
    silence, because both sides of the comparison are ours. A code added later classifies itself.
    """

    # The caller's own request, or the document it asked for.
    request = "request"
    # The inline template the caller shipped. Caller-side like `request`, and equally not ours: the
    # split says which half of the same payload to fix, and neither value is worth paging for.
    template = "template"
    # Prelum is shedding load by design. Nothing is broken, so this is never an incident — which is
    # why it cannot be folded into `service`.
    capacity = "capacity"
    # Prelum's own failure: the only value that should raise an alert.
    service = "service"


class AppError(Exception):
    status: HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR
    title: str = "Internal Server Error"

    # A stable identifier for this failure, and the thing callers are meant to branch on. Status
    # cannot serve that purpose: several different errors answer 413, and a caller telling "your
    # image is too large" from "the rendered document is too large" has nothing else to key on.
    # `title` is prose for humans and may be reworded; `code` is a published contract and may not.
    code: str = "internal_error"

    # Defaults to the loud answer: a class that forgets to declare its origin pages us rather than
    # quietly reporting our defect as the caller's. tests/test_error_codes.py refuses the default.
    origin: Origin = Origin.service

    # Prose for humans. Never structured, never contractual: callers read `context` instead.
    detail: str

    _context: Mapping[str, object]

    def __init__(self, message: str | None = None, *, context: Mapping[str, object] | None = None) -> None:
        super().__init__(message or self.title)
        self.detail = message or self.title
        self._context = dict(context) if context else {}

    @property
    def context(self) -> Mapping[str, object]:
        """The values this failure already holds, published beside `detail` for callers to act on.

        Each error class documents its own key set in docs/api/errors.md; the names mean the same
        thing wherever they appear (`limit` is always the configured limit, `size` always bytes).
        """
        return self._context

    @property
    def is_server_fault(self) -> bool:
        # Derived from origin rather than status so one classification drives both the published
        # field and the log level that is the only path to Sentry. `service` is exactly the 5xx set,
        # so this is the same answer status gave — it just cannot drift from what callers are told.
        return self.origin is Origin.service

    def to_response(self, request: Request) -> JSONResponse:
        # `context` is added after the standard members so a subclass cannot shadow `code`,
        # `origin` or `status`, and it is always present so callers never test for it before
        # reading it. `origin` is written as its value: a caller compares it to a string literal.
        body: dict[str, object] = {
            "code": self.code,
            "origin": self.origin.value,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": str(request.url),
            "context": dict(self.context),
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
    origin: Origin = Origin.request


class UnsupportedFormatError(AppError):
    status: HTTPStatus = HTTPStatus.BAD_REQUEST
    title: str = "Unsupported Format"
    code: str = "unsupported_format"
    origin: Origin = Origin.request


class InvalidTemplatePathError(AppError):
    status: HTTPStatus = HTTPStatus.BAD_REQUEST
    title: str = "Invalid Template Path"
    code: str = "invalid_template_path"
    # `request`, not `template`: a malformed `files` key is rejected in validation, before anything
    # compiles, so the fault is in the request's structure rather than in Typst source.
    origin: Origin = Origin.request


class RenderError(AppError):
    status: HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR
    title: str = "Render Failed"
    code: str = "render_failed"
    origin: Origin = Origin.service


class InlineTemplateError(AppError):
    """A caller-supplied inline template failed to compile — their input, not our fault."""

    status: HTTPStatus = HTTPStatus.UNPROCESSABLE_ENTITY
    title: str = "Inline Template Failed"
    code: str = "template_compile_failed"
    origin: Origin = Origin.template


class ServiceUnavailableError(AppError):
    status: HTTPStatus = HTTPStatus.SERVICE_UNAVAILABLE
    title: str = "Service Unavailable"
    code: str = "service_unavailable"
    origin: Origin = Origin.service


class ServiceOverloadedError(AppError):
    """Raised when no render slot became free within the configured queue wait.

    429 rather than 503 on purpose: this class declares `Origin.capacity`, so `is_server_fault` is
    false for it, and shedding load is designed behaviour, not a fault. A busy service should not
    page anyone. The Retry-After header travels with the response so the caller knows when to return.
    """

    status: HTTPStatus = HTTPStatus.TOO_MANY_REQUESTS
    title: str = "Too Many Requests"
    code: str = "render_queue_full"
    origin: Origin = Origin.capacity

    retry_after: int

    def __init__(self, *, retry_after: int) -> None:
        super().__init__("Render queue is full; retry shortly", context={"retry_after": retry_after})
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
    origin: Origin = Origin.request


class NotFoundError(AppError):
    status: HTTPStatus = HTTPStatus.NOT_FOUND
    title: str = "Not Found"
    code: str = "not_found"
    origin: Origin = Origin.request


class MethodNotAllowedError(AppError):
    status: HTTPStatus = HTTPStatus.METHOD_NOT_ALLOWED
    title: str = "Method Not Allowed"
    code: str = "method_not_allowed"
    origin: Origin = Origin.request


class StringTooLargeError(AppError):
    status: HTTPStatus = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title: str = "String Too Large"
    code: str = "string_too_large"
    origin: Origin = Origin.request

    size: int
    limit: int
    # Where in `data` the string sits: object keys as str, array indices as int. The renderer's
    # walk has no notion of its position, so each frame prepends its own segment as this unwinds.
    path: list[str | int]
    # An oversized object key is its own last path segment, which is indistinguishable from the
    # path to the value stored under it. This says which of the two the caller must shorten.
    subject: Literal["key", "value"]

    def __init__(self, *, size: int, limit: int, subject: Literal["key", "value"] = "value") -> None:
        super().__init__(f"String size {size} bytes exceeds limit {limit}")
        self.size = size
        self.limit = limit
        self.subject = subject
        self.path = []

    @property
    @override
    def context(self) -> Mapping[str, object]:
        return {"path": list(self.path), "subject": self.subject, "size": self.size, "limit": self.limit}


class InvalidFileDataError(AppError):
    status: HTTPStatus = HTTPStatus.BAD_REQUEST
    title: str = "Invalid File Data"
    code: str = "invalid_file_data"
    origin: Origin = Origin.request


class RequestTooLargeError(AppError):
    status: HTTPStatus = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title: str = "Request Too Large"
    code: str = "request_too_large"
    origin: Origin = Origin.request

    def __init__(self, *, limit: int, declared_size: int | None = None) -> None:
        # The body's true size is never known: the middleware stops reading at the limit. The only
        # size worth reporting is the one the caller declared, named so nobody mistakes it for ours.
        context: dict[str, object] = {"limit": limit}
        if declared_size is not None:
            context["declared_size"] = declared_size
        super().__init__(f"Request body exceeds {limit} bytes", context=context)


class RenderTimeoutError(AppError):
    status: HTTPStatus = HTTPStatus.REQUEST_TIMEOUT
    title: str = "Render Timeout"
    code: str = "render_timeout"
    # `request`, not `service`: a slow host could also explain a 408, but render time is driven by
    # the caller's own document, so the caller's side is the one that reads as the more likely cause.
    origin: Origin = Origin.request

    def __init__(self, *, timeout_secs: int) -> None:
        super().__init__(f"Render timed out after {timeout_secs} seconds", context={"timeout_secs": timeout_secs})


class OutputTooLargeError(AppError):
    status: HTTPStatus = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title: str = "Output Too Large"
    code: str = "output_too_large"
    # The output is the caller's template and data together, so `template` would be as defensible.
    # `request` wins because the caller reaches for `pages`, `data` and `output` first, and neither
    # value pages anyone, so the choice is advisory. Same for TooManyOutputFilesError.
    origin: Origin = Origin.request

    def __init__(self, *, size: int, limit: int) -> None:
        super().__init__(f"Output size {size} bytes exceeds limit {limit}", context={"size": size, "limit": limit})


class PageSelectionTooLargeError(AppError):
    """The caller's `pages` names more pages than one archive may hold; nothing was rendered."""

    status: HTTPStatus = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title: str = "Page Selection Too Large"
    code: str = "page_selection_too_large"
    origin: Origin = Origin.request

    def __init__(self, *, count: int, limit: int) -> None:
        super().__init__(
            f"Page selection contains {count} pages; limit is {limit}",
            context={"count": count, "limit": limit},
        )


class TooManyOutputFilesError(AppError):
    """The document produced more pages than one archive may hold.

    No count is published: Typst is asked for at most `limit + 1` pages, the extra one a sentinel,
    so the number seen here is always that constant and never the document's real page count.
    """

    status: HTTPStatus = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title: str = "Too Many Output Files"
    code: str = "too_many_output_files"
    origin: Origin = Origin.request

    def __init__(self, *, limit: int) -> None:
        super().__init__(f"Output file count exceeds limit {limit}", context={"limit": limit})


class TemplateSourceTooLargeError(AppError):
    status: HTTPStatus = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title: str = "Template Too Large"
    code: str = "template_source_too_large"
    origin: Origin = Origin.template

    def __init__(self, *, size: int, limit: int) -> None:
        super().__init__(
            f"Template source size {size} bytes exceeds limit {limit}",
            context={"size": size, "limit": limit},
        )


class TemplateFileTooLargeError(AppError):
    status: HTTPStatus = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    title: str = "Template Too Large"
    code: str = "template_file_too_large"
    origin: Origin = Origin.template

    def __init__(self, *, key: str, size: int, limit: int) -> None:
        # The prose shortens the key; the context carries it whole. It passed
        # validate_inline_file_key, so it is a bounded, whitelisted path the caller itself sent.
        super().__init__(
            f"Template file '{for_message(key)}' size {size} bytes exceeds limit {limit}",
            context={"key": key, "size": size, "limit": limit},
        )


def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger = cast(FilteringBoundLogger, structlog.get_logger())
    log_method: Callable[..., None] = logger.error if exc.is_server_fault else logger.warning

    log_method(
        "app.error",
        status=exc.status.value,
        title=exc.title,
        detail=exc.detail,
        # One structured field, not spread: spreading would collide with `status` and `title`.
        context=dict(exc.context),
        path=str(request.url),
        method=request.method,
    )

    return exc.to_response(request)


def error_codes() -> list[dict[str, object]]:
    """
    Every published error code with its status and origin, for the documentation and its artefact.

    Deployment-independent by construction: these are properties of the classes, not of a running
    service. `AppError` itself is excluded — it carries `internal_error` and never reaches a caller,
    since every 5xx arrives as `render_failed` or `service_unavailable`.
    """

    def descendants(cls: type[AppError]) -> Iterator[type[AppError]]:
        for subclass in cls.__subclasses__():
            if subclass.__module__ == __name__:
                yield subclass
            yield from descendants(subclass)

    return sorted(
        (
            {"code": cls.code, "status": int(cls.status), "origin": cls.origin.value}
            for cls in set(descendants(AppError))
        ),
        key=lambda entry: cast(str, entry["code"]),
    )
