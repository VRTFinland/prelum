"""Load shedding: a full queue must reject quickly rather than let callers wait without bound.

Before this, `async with semaphore` waited forever. Under sustained overload every caller waited
longer, held a connection, and the render timeout never helped because it only starts once a permit
has been acquired. These tests pin the bounded wait, the 429, and — most importantly — that a shed
request does not leak the permit it was waiting for.
"""

import asyncio
from collections.abc import Iterator

import httpx2 as httpx
import pytest

from app import deps
from app.core.config import Settings
from app.core.errors import ServiceOverloadedError
from app.main import create_app
from app.render.renderer import RenderResult

TOKEN = {"X-Prelum-Api-Token": "dev-only-insecure-token"}
SOURCE = '#text("hi")'


def _result() -> RenderResult:
    return RenderResult(bytes=b"%PDF-1.4 fake", content_type="application/pdf", filename="test.pdf")


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    deps.get_settings.cache_clear()
    deps.get_render_semaphore.cache_clear()
    deps.get_renderer.cache_clear()
    yield
    deps.get_settings.cache_clear()
    deps.get_render_semaphore.cache_clear()
    deps.get_renderer.cache_clear()


@pytest.fixture
def shedding_app(monkeypatch: pytest.MonkeyPatch):
    """An app whose queue gives up almost immediately, so the tests do not sleep for seconds."""
    monkeypatch.setenv("PRELUM_MAX_QUEUE_WAIT_SECS", "0.05")
    monkeypatch.setenv("PRELUM_RETRY_AFTER_SECS", "5")
    monkeypatch.setenv("PRELUM_MAX_CONCURRENT_RENDERS", "1")
    deps.get_settings.cache_clear()
    deps.get_render_semaphore.cache_clear()
    return create_app()


async def _post(app, semaphore: asyncio.Semaphore | None = None) -> httpx.Response:
    if semaphore is not None:
        app.dependency_overrides[deps.get_render_semaphore] = lambda: semaphore
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/v1/render", json={"source": SOURCE}, headers=TOKEN)


@pytest.mark.asyncio
async def test_a_full_queue_sheds_with_429(shedding_app):
    """The whole point: a caller that cannot be served promptly is told so, not left waiting."""
    exhausted = asyncio.Semaphore(1)
    await exhausted.acquire()

    response = await _post(shedding_app, exhausted)

    assert response.status_code == 429
    assert response.json()["title"] == "Too Many Requests"


@pytest.mark.asyncio
async def test_shed_response_carries_retry_after(shedding_app):
    exhausted = asyncio.Semaphore(1)
    await exhausted.acquire()

    response = await _post(shedding_app, exhausted)

    assert "Retry-After" in response.headers
    assert response.headers["Retry-After"].isdigit()


@pytest.mark.asyncio
async def test_retry_after_stays_within_the_configured_band(shedding_app):
    """The configured value is a floor: a caller is never asked to come back sooner than that."""
    exhausted = asyncio.Semaphore(1)
    await exhausted.acquire()

    values = set()
    for _ in range(40):
        response = await _post(shedding_app, exhausted)
        values.add(int(response.headers["Retry-After"]))

    assert min(values) >= 5
    assert max(values) <= 8


@pytest.mark.asyncio
async def test_retry_after_is_jittered(shedding_app):
    """A single value would send every shed caller back at the same instant."""
    exhausted = asyncio.Semaphore(1)
    await exhausted.acquire()

    values = {int((await _post(shedding_app, exhausted)).headers["Retry-After"]) for _ in range(40)}

    assert len(values) > 1, f"Retry-After never varied across 40 sheds: {values}"


@pytest.mark.asyncio
async def test_shedding_does_not_leak_the_permit(shedding_app, monkeypatch: pytest.MonkeyPatch):
    """
    The failure that would matter most: asyncio.wait_for cancels the pending acquire, and if the
    semaphore granted the permit as the cancellation landed, that permit must be given back. A leak
    would shrink capacity on every shed until the service rejected everything.
    """
    from app.render.renderer import TypstRenderer

    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()

    shed = await _post(shedding_app, semaphore)
    assert shed.status_code == 429

    semaphore.release()

    async def fake_render(self, job):
        return _result()

    monkeypatch.setattr(TypstRenderer, "render", fake_render)
    after = await _post(shedding_app, semaphore)

    assert after.status_code == 200, "the permit was lost when the queued request was shed"
    assert semaphore._value == 1, f"permit count drifted to {semaphore._value}"


@pytest.mark.asyncio
async def test_capacity_within_limits_is_not_shed(shedding_app, monkeypatch: pytest.MonkeyPatch):
    """Shedding must be the exception; a request that fits is served normally."""
    from app.render.renderer import TypstRenderer

    async def fake_render(self, job):
        return _result()

    monkeypatch.setattr(TypstRenderer, "render", fake_render)

    response = await _post(shedding_app, asyncio.Semaphore(1))

    assert response.status_code == 200


def test_settings_expose_the_queue_wait_and_retry_after():
    settings = Settings(environment="test")

    assert settings.max_queue_wait_secs == 10.0
    assert settings.retry_after_secs == 5


def test_service_overloaded_error_is_not_a_server_fault():
    """429 keeps this out of the incident path, so a busy service does not page anyone."""
    error = ServiceOverloadedError(retry_after=5)

    assert error.status == 429
    assert error.is_server_fault is False
