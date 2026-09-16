import os
import subprocess
import uuid
from http import HTTPStatus
from typing import cast

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, ExceptionHandler, Message, Receive, Scope, Send
from structlog.typing import FilteringBoundLogger

from app.api.routes import router
from app.core import constants
from app.core.config import Settings
from app.core.errors import (
    VALIDATION_ERRORS_BY_CODE,
    AppError,
    InvalidRequestError,
    MethodNotAllowedError,
    NotFoundError,
    RequestTooLargeError,
    ServiceUnavailableError,
    app_error_handler,
    for_message,
)
from app.core.logging import bind_request_context, clear_request_context, setup_logging
from app.core.sentry import setup_sentry
from app.deps import get_settings
from app.render.renderer import resource_limit_args


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


# Both middlewares below are plain ASGI rather than BaseHTTPMiddleware subclasses. That base wraps
# every request in an anyio task group and a pair of memory object streams, measured here at about
# 190us per layer per request against a bare app answering /health in 153us — so two layers more
# than doubled the cost of every request, for a byte counter and a header copy. /v1/render never
# noticed it against a typst fork; /health, /metrics and /v1/constraints paid it in full.


class BodyLimitMiddleware:
    """
    Reject an oversized request body while it is still arriving.

    Counts the chunks actually received rather than trusting Content-Length, and raises as soon
    as the total passes max_body — so a 20 MB inline template is refused mid-stream instead of
    being buffered whole and then rejected.
    """

    def __init__(self, app: ASGIApp, max_body: int) -> None:
        self.app: ASGIApp = app
        self.max_body: int = max_body

    def _too_large(self, scope: Scope) -> RequestTooLargeError:
        return RequestTooLargeError(limit=self.max_body, declared_size=_declared_size(Request(scope)))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        received = 0
        refused = False

        async def receive_with_limit() -> Message:
            nonlocal received, refused
            message = await receive()
            if message["type"] != "http.request":
                return message

            body = message.get("body", b"") or b""
            received += len(body)
            if received > self.max_body:
                refused = True
                raise self._too_large(scope)

            return message

        async def send_unless_refused(message: Message) -> None:
            # FastAPI wraps anything raised while it reads the body into a bare HTTPException(400),
            # so the error above never reaches the handler registered for it. Once the body has been
            # refused, whatever the app produced answers a request that is not being served, so it
            # is dropped here — nothing reaches the server — and replaced below. Dropping rather
            # than replacing is what plain ASGI allows: there is no buffered response to swap.
            if not refused:
                await send(message)

        try:
            await self.app(scope, receive_with_limit, send_unless_refused)
        except RequestTooLargeError:
            # Only the refusal above is answered here — the route that reads the body itself, with
            # FastAPI's wrapping out of the way. Anything else raising this error has its own reason
            # and its own response to send, and swallowing it would leave the caller with no reply.
            if not refused:
                raise
        if refused:
            await app_error_handler(Request(scope), self._too_large(scope))(scope, receive, send)


class RequestIdMiddleware:
    """
    Carry a request id through the logging context and echo it back to the caller.

    The caller's own x-request-id is preserved so a trace spans the services in front of Prelum;
    absent one, a uuid4 is minted. Only `send` is wrapped: nothing here reads the body.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = Request(scope).headers.get("x-request-id") or str(uuid.uuid4())
        bind_request_context(request_id)
        # The same place request.state reads from, so a route can still reach it by that name.
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Assigned rather than appended, so the header is stated once even if a response
                # already carries one.
                MutableHeaders(scope=message)["x-request-id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            clear_request_context()


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

    The probe forks the renderer's own wrapper prefix — not a copy of it — against the configured
    Typst binary, so it also catches a cli_path that is missing or not executable, which is the same
    silent-422 failure by another route.

    :raises RuntimeError: If the probe does not exit cleanly.
    """
    if settings.max_render_memory_bytes is None:
        return

    argv = [*resource_limit_args(settings), str(settings.cli_path), "--version"]
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


# Registered by create_app rather than defined inside it: none of the four closes over anything
# there, and as nested functions they were most of its body and could not be reached without
# building a whole app to drive them through a client.


async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    return app_error_handler(request, exc)


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


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Dropping "input" keeps the caller's own template source and assets — which is what it
    # mirrors — out of responses, proxies and logs. "ctx" duplicates "msg" and exposes Pydantic
    # internals. loc, msg and type identify the problem without either.
    errors = cast(list[dict[str, object]], exc.errors())
    published_errors = [
        {"loc": _bounded_location(error), "msg": _bounded_message(error), "type": error.get("type")}
        for error in errors[:_MAX_PUBLISHED_ERRORS]
    ]
    classified = next(
        (
            (error, error_class)
            for error in errors
            if (error_class := VALIDATION_ERRORS_BY_CODE.get(cast(str, error.get("type")))) is not None
        ),
        None,
    )
    error_class: type[AppError] = InvalidRequestError if classified is None else classified[1]

    # The one error `detail`, `code` and `rule` all describe. `code` follows the classified
    # files-key error wherever it sits among the errors, so the other two follow it too; with
    # nothing classified, the first failure answers for all three. Reading them from different
    # errors reported a rule the `detail` beside it was not about.
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
    # thing separating them is `msg`, which is prose a caller may not branch on. Past
    # _MAX_PUBLISHED_ERRORS the id is the only trace of the rule left in the response.
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

    app: FastAPI = FastAPI(title="Prelum", version=constants.VERSION)
    app.include_router(router)
    app.add_middleware(BodyLimitMiddleware, max_body=settings.max_request_body_bytes)

    Instrumentator().instrument(app).expose(app)

    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, cast(ExceptionHandler, handle_app_error))
    app.add_exception_handler(StarletteHTTPException, cast(ExceptionHandler, handle_http_exception))
    app.add_exception_handler(RequestValidationError, cast(ExceptionHandler, handle_validation_error))

    return app


app = create_app()
