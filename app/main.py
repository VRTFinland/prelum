import os
import subprocess
import uuid
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import cast, override

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message
from structlog.typing import FilteringBoundLogger

from app.api.routes import router
from app.core import constants
from app.core.config import Settings
from app.core.errors import (
    AppError,
    InvalidFileDataError,
    InvalidRequestError,
    InvalidTemplatePathError,
    MethodNotAllowedError,
    NotFoundError,
    RequestTooLargeError,
    ServiceUnavailableError,
    UnsupportedFormatError,
    app_error_handler,
    for_message,
)
from app.core.logging import bind_request_context, clear_request_context, setup_logging
from app.core.sentry import setup_sentry
from app.deps import get_settings


def _declared_size(request: Request) -> int | None:
    """The caller's Content-Length as an integer, or None when absent or not a plain decimal."""
    header = request.headers.get("content-length")
    if header is None or not (header.isascii() and header.isdigit()):
        return None
    try:
        return int(header)
    except ValueError:
        # Python bounds decimal-to-integer conversion to protect the process from arbitrarily long
        # inputs. An HTTP header can cross that bound, but its diagnostic value is not worth turning
        # an otherwise ordinary oversized request into a 5xx.
        return None


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

    def _too_large(self, request: Request) -> RequestTooLargeError:
        return RequestTooLargeError(limit=self.max_body, declared_size=_declared_size(request))

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
                raise self._too_large(request)

            return message

        request._receive = receive_with_limit
        try:
            response = await call_next(request)
        except RequestTooLargeError as exc:
            return app_error_handler(request, exc)
        if exceeded:
            return app_error_handler(request, self._too_large(request))
        return response


# Pydantic composes each message itself and some quote the offending value: an invalid discriminator
# tag is embedded whole, so an unbounded one is mirrored back twice — once in `msg` and once in the
# `detail` built from it. 200 rather than for_message's 80 because the value sits inside prose worth
# keeping: the longest legitimate message measures 157 characters.
_VALIDATION_MESSAGE_LIMIT = 200

# One malformed `files` entry yields two errors, so a mapping at the key cap can produce thousands
# of them — each bounded in length, but unbounded in number, which is amplification by another
# route. A caller fixes the first few and sends the request again; `errors_total` tells it how many
# there were, so a truncated list is never mistaken for a complete one.
_MAX_PUBLISHED_ERRORS = 20

# The probe runs `typst --version` under the wrapper; anything slower than this is a sick host.
_PROBE_TIMEOUT_SECS = 30

_MEMORY_LIMIT_VARIABLE = "PRELUM_MAX_RENDER_MEMORY_BYTES"


def _bounded_message(error: dict[str, object]) -> str:
    return for_message(str(error.get("msg", "Invalid request")), limit=_VALIDATION_MESSAGE_LIMIT)


def _bounded_location(error: dict[str, object]) -> list[str | int]:
    """
    One validation error's location with its caller-supplied segments bounded.

    A Pydantic loc names a mapping key verbatim, and a `files` key that fails field validation never
    reaches `validate_inline_file_key` — so this is the one place an unbounded key would otherwise
    be copied whole into the response body, the log line and Sentry. Array indices are Prelum's own
    count rather than caller text, and a caller reads them as positions, so they stay integers.
    """
    loc = cast(tuple[object, ...], error.get("loc", ()))
    return [part if isinstance(part, int) else for_message(str(part)) for part in loc]


# Starlette raises these two itself, before any route runs. Every other HTTPException is classified
# by status alone, because nothing else about it is contractual.
_HTTP_EXCEPTION_ERRORS: dict[int, type[AppError]] = {
    HTTPStatus.NOT_FOUND: NotFoundError,
    HTTPStatus.METHOD_NOT_ALLOWED: MethodNotAllowedError,
}


def _validation_detail(error: dict[str, object]) -> str:
    """One line of prose for the first validation error: `output.format: Input should be …`."""
    location = _bounded_location(error)
    # Every request field lives in the body, so the constant prefix says nothing.
    parts = [str(part) for part in (location[1:] if location[:1] == ["body"] else location)]
    message = _bounded_message(error)
    return f"{'.'.join(parts)}: {message}" if parts else message


def _verify_render_memory_limit(settings: Settings) -> None:
    """
    Prove the memory-limit wrapper works before the first request, or refuse to serve.

    Every failure of the wrapper itself exits with a positive status — a missing binary, a seccomp
    policy denying prlimit(2), an argument getopt declined to attach — and the renderer classifies a
    positive status as the caller's template failing to compile. Left undetected, a misconfigured
    limit answers 422 to everyone and pages nobody. One fork at startup converts all of it into a
    loud boot failure.

    The probe runs the real wrapper argv against the configured Typst binary, so it also catches a
    cli_path that is missing or not executable, which is the same silent-422 failure by another
    route.

    :raises RuntimeError: If the probe does not exit cleanly.
    """
    if settings.max_render_memory_bytes is None:
        return

    argv = [
        str(constants.PRLIMIT_PATH),
        f"--data={settings.max_render_memory_bytes}",
        "--",
        str(settings.cli_path),
        "--version",
    ]
    try:
        probe = subprocess.run(argv, capture_output=True, check=False, timeout=_PROBE_TIMEOUT_SECS)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as exc:
        # SubprocessError covers TimeoutExpired, which is not an OSError: a wrapper that hangs is
        # the sick host the timeout anticipates, and it must surface as the same startup failure
        # the documentation tells operators to look for rather than as a raw traceback.
        raise RuntimeError(f"the render memory limit probe could not run: {exc}") from exc
    if probe.returncode != 0:
        raise RuntimeError(
            f"the render memory limit probe exited {probe.returncode}; "
            f"PRELUM_MAX_RENDER_MEMORY_BYTES cannot be honoured on this deployment"
        )


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

    if settings.max_render_memory_bytes is None and _MEMORY_LIMIT_VARIABLE in os.environ:
        # Distinguished from an absent variable on purpose. Unset is the default everywhere the
        # wrapper cannot run, and warning about it would be noise on every development machine. An
        # empty value is different: something set it, so either an operator disabled the bound
        # deliberately or a template rendered an undefined value into it. That used to fail loudly
        # as an integer parse error; accepting it as the documented escape hatch made it silent,
        # and a deployment running unbounded also reports a container-level kill as the caller's
        # fault, because the renderer's classification keys off the same attribute.
        startup_logger.warning(
            "startup.render_memory_unbounded",
            message=(
                f"{_MEMORY_LIMIT_VARIABLE} is set but empty, so renders run without a memory bound. "
                "Remove the variable to say the same thing, or give it a value."
            ),
        )

    _verify_render_memory_limit(settings)

    app: FastAPI = FastAPI(title="Prelum", version="1.0.1")
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

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Starlette answers routing failures itself, and FastAPI raises a bare 400 for a body no
        # JSON parser accepts — a number past Python's decimal-to-integer bound, say, which is not
        # a JSONDecodeError and so never becomes a RequestValidationError. All of them bypassed the
        # two handlers above and replied in Starlette's own {"detail": ...} shape, contradicting the
        # published promise that every failure is application/problem+json.
        error_class = _HTTP_EXCEPTION_ERRORS.get(exc.status_code)
        # exc.detail is Starlette's prose and is dropped: a route may raise HTTPException with
        # caller-derived text, and no request content may re-enter a response.
        if error_class is None:
            # Nothing else about an arbitrary HTTPException is contractual, so it is classified by
            # status alone — which means the answer no longer carries the status that was raised.
            # Its headers belong to that status (a 401's WWW-Authenticate, say) and would be a
            # false instruction on this one, so they are dropped with it.
            return (InvalidRequestError if exc.status_code < 500 else ServiceUnavailableError)().to_response(request)

        # The status survives, so the headers still describe the answer: a 405 without its Allow
        # no longer tells the caller what to send instead.
        response = error_class().to_response(request)
        response.headers.update(exc.headers or {})
        return response

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Dropping "input" keeps the caller's own template source and assets — which is what it
        # mirrors — out of responses, proxies and logs. "ctx" duplicates "msg" and exposes Pydantic
        # internals. loc, msg and type identify the problem without either.
        errors = cast(list[dict[str, object]], exc.errors())
        published_errors = [
            {"loc": _bounded_location(error), "msg": _bounded_message(error), "type": error.get("type")}
            for error in errors[:_MAX_PUBLISHED_ERRORS]
        ]
        classified_errors: dict[str, type[AppError]] = {
            InvalidTemplatePathError.code: InvalidTemplatePathError,
            InvalidFileDataError.code: InvalidFileDataError,
        }
        classified = next(
            (
                (error, error_class)
                for error in errors
                if (error_class := classified_errors.get(cast(str, error.get("type")))) is not None
            ),
            None,
        )
        error_class: type[AppError] = InvalidRequestError if classified is None else classified[1]
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

        # The one error `detail`, `code` and `rule` all describe. `code` follows the classified
        # files-key error wherever it sits, so that error — not errors[0] — is what the other two
        # are read from as well; with nothing classified, the first failure answers for all three.
        # Picking the three from different errors is what produced "source: Field required" beside
        # a rule naming the output option, and a client doing what the documentation says (branch
        # on `rule`, not on `detail`) then reported the wrong cause. Today the two cannot even
        # coexist — the files-key checks run in RenderRequest's model validators, which Pydantic
        # skips once a field such as `output` has failed — but that is an accident of where the
        # checks happen to live, and moving one into a field validator on `files` would bring the
        # mismatch straight back. Reading all three from one error does not depend on it.
        reported = classified[0] if classified is not None else (errors[0] if errors else None)

        context: dict[str, object] = {"errors": published_errors}
        if len(errors) > _MAX_PUBLISHED_ERRORS:
            context["errors_total"] = len(errors)
        if classified is not None:
            # templates.py raised with a context; models.py carried it here through the Pydantic ctx.
            # "message" is the prose already in msg.
            lifted = cast(dict[str, object], classified[0].get("ctx", {}))
            context.update({key: value for key, value in lifted.items() if key != "message"})
        # An output-option rule keeps the `value_error` type every other validator failure has, so
        # `loc`, `type` and `code` are the same whichever of them fired: without the id, the only
        # thing separating them is `msg`, which is prose a caller may not branch on. The id is
        # therefore published, and past _MAX_PUBLISHED_ERRORS it is the only trace of the rule left
        # — the named error need not appear in `errors` at all. A rule carried by some other error
        # is dropped rather than reported: the caller has not been told about that failure yet.
        rule = cast(dict[str, object], reported.get("ctx", {})).get("rule") if reported is not None else None
        if isinstance(rule, str):
            context["rule"] = rule

        # Validation errors carry the raised exception in ctx; encode at any depth so the response
        # body cannot fail to serialise.
        error = error_class(
            _validation_detail(reported) if reported is not None else "Request validation failed",
            context=cast(dict[str, object], jsonable_encoder(context, custom_encoder={Exception: str})),
        )
        return error.to_response(request)

    return app


app = create_app()
