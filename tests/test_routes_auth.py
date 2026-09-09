from unittest.mock import AsyncMock, patch

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.core.errors import InlineTemplateError
from app.main import app, create_app
from app.render.renderer import RenderResult, TypstRenderer

client = TestClient(app)
TOKEN = {"X-Prelum-Api-Token": "dev-only-insecure-token"}
SOURCE = '#text("hi")'


def _result() -> RenderResult:
    return RenderResult(bytes=b"%PDF-1.4 fake", content_type="application/pdf", filename="test.pdf")


def test_body_limit_checks_actual_bytes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRELUM_MAX_REQUEST_BODY_BYTES", "1024")
    deps.get_settings.cache_clear()
    try:
        limited_client = TestClient(create_app())
        response = limited_client.post(
            "/v1/render",
            json={"source": SOURCE, "data": "x" * 2048},
            headers={**TOKEN, "Content-Length": "5"},
        )
        assert response.status_code == 413
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "request_too_large"
    finally:
        deps.get_settings.cache_clear()


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_render_requires_source_field():
    response = client.post("/v1/render", json={"data": {}}, headers=TOKEN)
    assert response.status_code == 400
    assert response.json()["title"] == "Invalid Request"


def test_legacy_render_endpoint_does_not_exist():
    assert client.post("/render", json={"source": SOURCE}, headers=TOKEN).status_code == 404


def test_render_response_headers():
    with patch.object(TypstRenderer, "render", new=AsyncMock(return_value=_result())):
        response = client.post("/v1/render", json={"source": SOURCE}, headers=TOKEN)

    assert response.status_code == 200
    assert response.headers["Content-Disposition"] == 'inline; filename="test.pdf"'
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Content-Type"] == "application/pdf"


def test_render_uses_the_result_disposition_for_an_archive():
    result = RenderResult(
        bytes=b"PK\x03\x04fake",
        content_type="application/zip",
        filename="pages.zip",
        disposition="attachment",
    )
    with patch.object(TypstRenderer, "render", new=AsyncMock(return_value=result)):
        response = client.post(
            "/v1/render",
            json={"source": SOURCE, "output": {"format": "svg", "archive": "zip"}},
            headers=TOKEN,
        )

    assert response.status_code == 200
    assert response.headers["Content-Disposition"] == 'attachment; filename="pages.zip"'
    assert response.headers["Content-Type"] == "application/zip"


def test_render_semaphore_releases_on_error():
    with patch.object(
        TypstRenderer,
        "render",
        new=AsyncMock(side_effect=InlineTemplateError("Template rendering failed")),
    ):
        for index in range(3):
            response = client.post("/v1/render", json={"source": "#broken("}, headers=TOKEN)
            assert response.status_code == 422, f"Request {index + 1} failed with {response.status_code}"


def test_render_rejects_a_non_ascii_token_with_403_not_a_5xx():
    headers = httpx.Headers([(b"X-Prelum-Api-Token", "tökén-ñ".encode())])
    response = client.post("/v1/render", json={"source": SOURCE}, headers=headers)
    assert response.status_code == 403
    assert response.json()["title"] == "Forbidden"


def test_render_requires_api_token():
    response = client.post("/v1/render", json={"source": SOURCE})
    assert response.status_code == 403
    assert response.json()["title"] == "Forbidden"


def test_render_validation_error_does_not_echo_the_request_body():
    response = client.post(
        "/v1/render",
        json={
            "source": "SECRETMARKER" * 100,
            "files": {"../bad.typ": {"encoding": "text", "content": "x"}},
        },
        headers=TOKEN,
    )
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "SECRETMARKER" not in response.text
    assert "bad.typ" in response.text


def test_render_inline_compile_failure_returns_422_problem():
    with patch.object(
        TypstRenderer,
        "render",
        new=AsyncMock(side_effect=InlineTemplateError("Template rendering failed")),
    ):
        response = client.post("/v1/render", json={"source": "#broken("}, headers=TOKEN)

    assert response.status_code == 422
    assert response.json()["title"] == "Inline Template Failed"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_render_rejects_conflicting_file_keys_with_400():
    response = client.post(
        "/v1/render",
        json={
            "source": SOURCE,
            "files": {
                "lib": {"encoding": "text", "content": "x"},
                "lib/utils.typ": {"encoding": "text", "content": "y"},
            },
        },
        headers=TOKEN,
    )
    assert response.status_code == 400
