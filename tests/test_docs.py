import json
from pathlib import Path

from scripts.export_openapi import export_openapi

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"


def test_openapi_export_describes_routes_models_and_authentication(tmp_path: Path):
    destination = tmp_path / "openapi.json"

    export_openapi(destination)

    schema = json.loads(destination.read_text(encoding="utf-8"))
    assert {"/health", "/v1/render"} <= schema["paths"].keys()
    assert schema["info"]["title"] == "Prelum"
    assert schema["info"]["version"] == "1.0.0"

    security_schemes = schema["components"]["securitySchemes"]
    assert security_schemes == {
        "PrelumApiToken": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Prelum-Api-Token",
        }
    }
    assert schema["paths"]["/v1/render"]["post"]["security"] == [{"PrelumApiToken": []}]

    responses = schema["paths"]["/v1/render"]["post"]["responses"]
    assert set(responses["200"]["content"]) == {
        "application/pdf",
        "application/zip",
        "image/png",
        "image/svg+xml",
    }
    for status in ("400", "403", "408", "413", "422", "429", "500", "503"):
        assert set(responses[status]["content"]) == {"application/problem+json"}

    render_request = schema["components"]["schemas"]["RenderRequest"]
    assert "source" in render_request["required"]


def test_makefile_owns_the_shared_documentation_commands():
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "docs-build:" in makefile
    assert "docs-serve:" in makefile
    assert "python -m scripts.export_openapi" in makefile
    assert "mkdocs build --strict" in makefile
