"""The examples are the caller-facing contract; an untested example is a stale example."""

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import RenderRequest
from scripts.render_examples import EXAMPLE_SET, Example, example_data, example_files

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
TOKEN = {"X-Prelum-Api-Token": "dev-only-insecure-token"}

_REGENERATE_HINT = (
    "render-request.json is a generated file, not a hand-maintained one: rerun "
    "`uv run python scripts/regenerate-example-request.py` after editing the .typ files."
)


def _request_body() -> dict[str, object]:
    return json.loads((EXAMPLES / "render-request.json").read_text(encoding="utf-8"))


def test_example_request_matches_the_example_typst_files():
    """The .typ files and the JSON body must not drift apart."""
    body = _request_body()

    assert body["source"] == (EXAMPLES / "hello.typ").read_text(encoding="utf-8"), _REGENERATE_HINT
    assert body["files"]["lib/label.typ"]["content"] == (EXAMPLES / "lib" / "label.typ").read_text(encoding="utf-8"), (
        _REGENERATE_HINT
    )


def test_example_request_is_a_valid_render_request():
    request = RenderRequest.model_validate(_request_body())

    assert request.files["lib/label.typ"].encoding == "text"


@pytest.mark.integration
def test_example_request_renders_a_pdf_through_the_api():
    with TestClient(app) as client:
        response = client.post("/v1/render", json=_request_body(), headers=TOKEN)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


@pytest.mark.integration
@pytest.mark.parametrize("example", EXAMPLE_SET, ids=lambda example: example.name)
def test_site_example_renders_through_the_api(example: Example):
    """Every published example must survive the real endpoint, not only the renderer it is built with."""
    body = {
        "source": (EXAMPLES / example.name / "main.typ").read_text(encoding="utf-8"),
        "files": {key: file.model_dump() for key, file in example_files(example).items()},
        "data": example_data(example),
        "output": {"format": "pdf"},
    }

    with TestClient(app) as client:
        response = client.post("/v1/render", json=body, headers=TOKEN)

    assert response.status_code == 200, response.text
    assert response.content.startswith(b"%PDF")


def test_example_assets_are_encoded_by_kind():
    """A font is bytes and an SVG is text; sending either in the wrong encoding corrupts it."""
    manual = next(example for example in EXAMPLE_SET if example.name == "manual")

    files = example_files(manual)

    assert files["assets/prelum-logo.svg"].encoding == "text"
    assert files["fonts/SpaceGrotesk-Bold.ttf"].encoding == "base64"
    assert base64.b64decode(files["fonts/SpaceGrotesk-Bold.ttf"].content)[:4] == b"\x00\x01\x00\x00"
