import json
from unittest.mock import AsyncMock, patch

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.core.errors import InlineTemplateError
from app.core.output_rules import output_rules as published_output_rules
from app.main import app, create_app
from app.render.renderer import RenderResult, TypstRenderer
from app.render.templates import MAX_INLINE_FILE_KEYS

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
        # The header is the caller's own declaration; Prelum measured nothing it can report.
        assert response.json()["context"] == {"limit": 1024, "declared_size": 5}
    finally:
        deps.get_settings.cache_clear()


def test_body_limit_reports_only_the_limit_without_content_length(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRELUM_MAX_REQUEST_BODY_BYTES", "1024")
    deps.get_settings.cache_clear()
    try:
        limited_client = TestClient(create_app())
        body = json.dumps({"source": SOURCE, "data": "x" * 2048}).encode()
        # An iterable body is sent chunked, so no Content-Length header exists to declare a size.
        response = limited_client.post(
            "/v1/render",
            content=iter([body]),
            headers={**TOKEN, "Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["code"] == "request_too_large"
        assert response.json()["context"] == {"limit": 1024}
    finally:
        deps.get_settings.cache_clear()


def test_body_limit_does_not_fail_on_an_unconvertibly_long_content_length(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRELUM_MAX_REQUEST_BODY_BYTES", "1024")
    deps.get_settings.cache_clear()
    try:
        limited_client = TestClient(create_app())
        response = limited_client.post(
            "/v1/render",
            json={"source": SOURCE, "data": "x" * 2048},
            headers={**TOKEN, "Content-Length": "9" * 5000},
        )

        assert response.status_code == 413
        assert response.json()["context"] == {"limit": 1024}
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


def test_constraints_requires_a_token():
    assert client.get("/v1/constraints").status_code == 403


def test_constraints_publishes_the_rules_and_this_instance_limits():
    response = client.get("/v1/constraints", headers=TOKEN)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    document = response.json()
    assert document["rules_version"] >= 1
    # Absolute, not '^': Ruby reads '^' as a line anchor and the document promises it as written.
    assert document["key_pattern"].startswith("\\A")
    assert document["limits"]["max_inline_files"] == 64
    assert "code" not in document["conformance_vectors"][0]
    assert "rule" not in document["conformance_vectors"][0]


def test_constraints_publishes_the_output_rules_under_their_own_version():
    """One fetch answers both contracts, but each keeps its own counter.

    A shared rules_version would send a caller back through the key rules because a PDF standard
    was added, and back through the output rules because a key rule changed.
    """
    document = client.get("/v1/constraints", headers=TOKEN).json()

    output_rules = document["output_rules"]
    assert output_rules == published_output_rules()
    assert "rules_version" not in output_rules
    assert output_rules["output_rules_version"] >= 1
    assert document["rules_version"] != output_rules["output_rules_version"]
    # The tables a mirror cannot derive, and the grammar behind `pages`.
    assert output_rules["pdf"]["pdf_a_version"]["a-2b"] == "1.7"
    assert output_rules["pdf"]["tagged_standards"] == ["a-1a", "a-2a", "a-3a", "ua-1"]
    assert output_rules["page_selection"]["max_selections"] == 64
    # exclude_none reaches the nested document too: an accepted vector carries no rejection fields.
    accepted = next(vector for vector in output_rules["conformance_vectors"] if vector["accepted"])
    assert set(accepted) == {"output", "accepted"}


def test_constraints_reflects_the_deployment_rather_than_the_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRELUM_MAX_INLINE_FILES", "7")
    deps.get_settings.cache_clear()
    try:
        limited_client = TestClient(create_app())
        document = limited_client.get("/v1/constraints", headers=TOKEN).json()

        assert document["limits"]["max_inline_files"] == 7
        assert document["limits"]["effective_max_files"] == 7
        assert document["max_keys"] == MAX_INLINE_FILE_KEYS
    finally:
        deps.get_settings.cache_clear()


def test_constraints_never_discloses_filesystem_paths(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRELUM_CLI_PATH", "/usr/local/hidden/typst")
    deps.get_settings.cache_clear()
    try:
        disclosing_client = TestClient(create_app())

        assert "hidden" not in disclosing_client.get("/v1/constraints", headers=TOKEN).text
    finally:
        deps.get_settings.cache_clear()


def test_constraints_appears_in_the_openapi_schema():
    operation = app.openapi()["paths"]["/v1/constraints"]["get"]

    assert operation["security"] == [{"PrelumApiToken": []}]
    responses = operation["responses"]
    assert responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ConstraintsResponse"
    }
    assert responses["403"]["content"]["application/problem+json"]["schema"]["title"] == "Problem"
