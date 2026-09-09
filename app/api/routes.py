import asyncio
import random
import secrets
import time
from typing import Annotated, Any, cast

import structlog
from fastapi import APIRouter, Depends, Security
from fastapi.responses import Response
from fastapi.security import APIKeyHeader
from structlog.typing import FilteringBoundLogger

from app.core.config import Settings
from app.core.constants import API_TOKEN_HEADER
from app.core.errors import (
    AppError,
    ForbiddenError,
    RenderTimeoutError,
    ServiceOverloadedError,
    ServiceUnavailableError,
)
from app.core.metrics import (
    concurrent_renders,
    output_size_bytes,
    render_duration_seconds,
    render_errors_total,
    render_queue_waiting,
    render_timeouts_total,
    render_total,
)
from app.deps import get_render_semaphore, get_renderer, get_settings
from app.models import RenderJob, RenderRequest
from app.render.renderer import TypstRenderer

router = APIRouter()
logger: FilteringBoundLogger = cast(FilteringBoundLogger, structlog.get_logger())
api_token_header = APIKeyHeader(name=API_TOKEN_HEADER, scheme_name="PrelumApiToken", auto_error=False)

_RENDER_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Rendered artefact",
        "content": {
            "application/pdf": {},
            "application/zip": {},
            "image/png": {},
            "image/svg+xml": {},
        },
    },
    **{
        status: {
            "description": "Problem response",
            "content": {"application/problem+json": {}},
        }
        for status in (400, 403, 408, 413, 422, 429, 500, 503)
    },
}


async def require_api_token(
    settings: Annotated[Settings, Depends(get_settings)],
    token: Annotated[str | None, Security(api_token_header)],
) -> None:
    """
    Validate the shared API token using constant-time comparison to prevent timing attacks.

    Comparison is done on the UTF-8 encoded bytes rather than the `str` values: `secrets.compare_digest`
    raises `TypeError` when given a `str` containing non-ASCII characters, which would otherwise turn a
    single non-ASCII byte in the header into a 5xx and a Sentry event from unauthenticated caller input.
    Encoding first keeps the comparison itself constant-time while making a non-ASCII token fail the
    same way as any other wrong token: a 403.
    """
    if not token or not settings.api_token:
        raise ForbiddenError("Invalid API token")
    if not secrets.compare_digest(token.encode("utf-8"), settings.api_token.encode("utf-8")):
        raise ForbiddenError("Invalid API token")


def _retry_after_secs(settings: Settings) -> int:
    """Pick a Retry-After value, jittered upwards from the configured floor.

    Retry-After only carries whole seconds, so the band has to be wide enough to survive rounding:
    a narrow one would collapse back to a single value and send every shed caller back at the same
    instant, producing the next spike. The configured value is the floor, never undercut.
    """
    base = settings.retry_after_secs
    return random.randint(base, round(base * 1.5))  # noqa: S311 - spreading retries, not security


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/v1/render", response_class=Response, responses=_RENDER_RESPONSES)
async def render(
    request: RenderRequest,
    renderer: Annotated[TypstRenderer, Depends(get_renderer)],
    semaphore: Annotated[asyncio.Semaphore, Depends(get_render_semaphore)],
    settings: Annotated[Settings, Depends(get_settings)],
    _token: Annotated[None, Depends(require_api_token)],
) -> Response:
    """
    Render a caller-supplied inline Typst source.

    Concurrent renders are capped by a semaphore (PRELUM_MAX_CONCURRENT_RENDERS): each one forks a
    typst process, so without the cap a burst of requests exhausts the container rather than queueing.

    :param request: The render request containing the inline template source, data, and options.
    :param renderer: The Typst renderer instance.
    :param semaphore: Semaphore for controlling concurrent render operations.
    :param _token: API token validation dependency.
    :return: A FastAPI Response object containing the rendered bytes with the appropriate content type and headers.
    :raises RenderTimeoutError: If the rendering process exceeds the configured timeout.
    :raises AppError: For known application-level rendering failures.
    :raises ServiceUnavailableError: For unexpected or unhandled errors during rendering.
    :raises asyncio.CancelledError: If the client disconnects or the request is cancelled.
    """
    job = RenderJob(source=request.source, files=request.files, data=request.data, output=request.output)
    output_format = job.output.format.value

    # Track queue waiting
    render_queue_waiting.inc()
    waiting = True

    try:
        # Bounded rather than `async with semaphore`: an unbounded wait turns an overload into
        # every caller waiting longer and longer, holding a connection, while the render timeout
        # below never starts because it only covers the subprocess. wait_for cancels the pending
        # acquire on timeout, and asyncio gives the permit back if it was granted as the
        # cancellation landed — tests/test_load_shedding.py pins that, because a leak here would
        # shrink capacity on every shed until nothing was served.
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=settings.max_queue_wait_secs)
        except TimeoutError:
            render_queue_waiting.dec()
            waiting = False
            raise ServiceOverloadedError(retry_after=_retry_after_secs(settings)) from None

        render_queue_waiting.dec()
        waiting = False
        concurrent_renders.inc()
        render_start = time.perf_counter()
        try:
            result = await renderer.render(job)
        finally:
            concurrent_renders.dec()
            semaphore.release()
            render_time = time.perf_counter() - render_start
            render_duration_seconds.labels(
                output_format=output_format,
            ).observe(render_time)
    except asyncio.CancelledError:  # pragma: no cover - safety
        if waiting:
            render_queue_waiting.dec()
        render_total.labels(
            output_format=output_format,
            status="cancelled",
        ).inc()
        raise
    except RenderTimeoutError:
        render_timeouts_total.inc()
        render_errors_total.labels(error_type="timeout").inc()
        render_total.labels(
            output_format=output_format,
            status="timeout",
        ).inc()
        raise
    except AppError as exc:
        render_errors_total.labels(error_type=type(exc).__name__).inc()
        render_total.labels(
            output_format=output_format,
            status="error",
        ).inc()
        raise
    except Exception as exc:
        logger.exception("render.unhandled_error", error=str(exc))
        render_errors_total.labels(error_type="unhandled").inc()
        render_total.labels(
            output_format=output_format,
            status="error",
        ).inc()
        raise ServiceUnavailableError("Render failed") from exc

    # Track successful render
    render_total.labels(
        output_format=output_format,
        status="success",
    ).inc()

    # Track output size
    output_size_bytes.labels(
        output_format=output_format,
    ).observe(len(result.bytes))

    headers = {
        "Content-Disposition": f'{result.disposition}; filename="{result.filename}"',
        "Cache-Control": "no-store",
    }
    return Response(
        content=result.bytes,
        media_type=result.content_type,
        headers=headers,
    )
