import json
from pathlib import Path

from app.core.constraints import files_key_rules
from app.core.errors import Origin, error_codes
from scripts.export_constraints import export_constraints
from scripts.export_error_codes import export_error_codes
from scripts.export_openapi import export_openapi

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"
DOCS_CONFIG = ROOT / "zensical.toml"
PYPROJECT = ROOT / "pyproject.toml"
ERRORS_DOC = ROOT / "docs" / "api" / "errors.md"


def test_openapi_export_describes_routes_models_and_authentication(tmp_path: Path):
    destination = tmp_path / "openapi.json"

    export_openapi(destination)

    schema = json.loads(destination.read_text(encoding="utf-8"))
    assert {"/health", "/v1/constraints", "/v1/render"} <= schema["paths"].keys()
    assert schema["info"]["title"] == "Prelum"
    assert schema["info"]["version"] == "1.0.1"

    security_schemes = schema["components"]["securitySchemes"]
    assert security_schemes == {
        "PrelumApiToken": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Prelum-Api-Token",
        }
    }
    assert schema["paths"]["/v1/render"]["post"]["security"] == [{"PrelumApiToken": []}]

    constraints = schema["paths"]["/v1/constraints"]["get"]["responses"]
    assert constraints["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ConstraintsResponse"
    }
    assert constraints["403"]["content"]["application/problem+json"]["schema"]["title"] == "Problem"
    constraints_schema = schema["components"]["schemas"]["ConstraintsResponse"]
    assert {
        "rules_version",
        "key_pattern",
        "set_rules",
        "conformance_vectors",
        "limits",
    } <= set(constraints_schema["required"])
    assert constraints_schema["properties"]["limits"] == {"$ref": "#/components/schemas/ConstraintLimits"}

    responses = schema["paths"]["/v1/render"]["post"]["responses"]
    assert set(responses["200"]["content"]) == {
        "application/pdf",
        "application/zip",
        "image/png",
        "image/svg+xml",
    }
    for status in ("400", "403", "408", "413", "422", "429", "500", "503"):
        problem = responses[status]["content"]
        assert set(problem) == {"application/problem+json"}
        problem_schema = problem["application/problem+json"]["schema"]
        assert set(problem_schema["required"]) == {
            "code",
            "origin",
            "title",
            "status",
            "detail",
            "instance",
            "context",
        }
        assert problem_schema["properties"]["detail"]["type"] == "string"
        assert problem_schema["properties"]["context"]["type"] == "object"

    render_request = schema["components"]["schemas"]["RenderRequest"]
    assert "source" in render_request["required"]


def test_makefile_owns_the_shared_documentation_commands():
    makefile = MAKEFILE.read_text(encoding="utf-8")
    pyproject = PYPROJECT.read_text(encoding="utf-8")

    assert "docs-build:" in makefile
    assert "docs-serve:" in makefile
    assert "python -m scripts.export_openapi" in makefile
    assert "zensical build --strict" in makefile
    assert '"zensical>=' in pyproject
    assert "mkdocs-material" not in pyproject


def test_internal_design_records_are_not_in_the_repository():
    """
    They are kept on disk and out of git, not merely out of the documentation source tree.

    The repository is public, so a record outside `docs/` is still readable by anyone; the earlier
    arrangement kept proposals off the site while publishing them in the tree. Zensical has no
    `exclude_docs`, so a build option cannot be the mechanism either.
    """
    config = DOCS_CONFIG.read_text(encoding="utf-8")

    assert "exclude_docs" not in config
    assert not (ROOT / "docs" / "design").exists()


def test_constraints_export_publishes_the_rules_without_deployment_configuration(tmp_path: Path):
    destination = tmp_path / "files-key-rules.json"

    export_constraints(destination)

    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document == files_key_rules()
    assert "limits" not in document, "the static artefact must not imply a deployment's limits"
    assert document["conformance_vectors"], "an artefact without vectors cannot be diffed against"


def test_makefile_generates_the_constraints_artefact_for_the_site():
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "python -m scripts.export_constraints" in makefile
    assert "docs-build: docs-examples docs-openapi docs-constraints" in makefile


def test_openapi_publishes_the_error_origin_and_its_values(tmp_path: Path):
    """A caller pinning a branch on origin needs the value set, not a bare string type."""
    destination = tmp_path / "openapi.json"

    export_openapi(destination)

    schema = json.loads(destination.read_text(encoding="utf-8"))
    # routes.py inlines Problem.model_json_schema() straight into each error response instead of
    # routing it through FastAPI's response_model machinery, so it never reaches
    # components.schemas as a named "Problem" component. _PROBLEM_SCHEMA is built once in routes.py
    # and reused by reference for every error response on both /v1/render and /v1/constraints, so
    # asserting on one response's schema covers them all.
    problem = schema["paths"]["/v1/render"]["post"]["responses"]["400"]["content"]["application/problem+json"][
        "schema"
    ]
    assert "origin" in problem["required"]
    # routes.py resolves each property's $defs reference before inlining the schema, since a $ref to
    # a sibling $defs entry would dangle once this schema sits under a response rather than under
    # components.schemas. The enum body must therefore appear directly on the property, and no
    # $ref/$defs pair may survive anywhere in the exported document — that dangling reference is
    # exactly the regression this test exists to catch.
    assert set(problem["properties"]["origin"]["enum"]) == {origin.value for origin in Origin}
    assert "$defs" not in problem
    assert "#/$defs/" not in json.dumps(schema)


def _documented_codes() -> dict[str, tuple[int, str]]:
    """Parse the Codes table into {code: (status, origin)}."""
    section = ERRORS_DOC.read_text(encoding="utf-8").split("## Codes", 1)[1]
    documented: dict[str, tuple[int, str]] = {}
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        documented[cells[1].strip("`")] = (int(cells[0]), cells[2].strip("`"))
    return documented


def test_every_error_code_is_documented_with_its_status_and_origin():
    """The table is what a caller reads instead of the source; a drifting row is the whole bug."""
    expected = {entry["code"]: (entry["status"], entry["origin"]) for entry in error_codes()}

    assert _documented_codes() == expected


def test_error_codes_export_publishes_the_taxonomy_without_deployment_configuration(tmp_path: Path):
    """A caller can pin the mapping without a token, exactly as it can pin the files-key rules."""
    destination = tmp_path / "error-codes.json"

    export_error_codes(destination)

    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document == error_codes()
    assert all(set(entry) == {"code", "status", "origin"} for entry in document)
    # A spot check on the family that motivated the field, so an empty or truncated artefact fails.
    assert {"code": "template_file_too_large", "status": 413, "origin": "template"} in document
    assert {"code": "page_selection_too_large", "status": 413, "origin": "request"} in document


def test_makefile_generates_the_error_codes_artefact_for_the_site():
    makefile = MAKEFILE.read_text(encoding="utf-8")
    phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY:"))

    assert "python -m scripts.export_error_codes" in makefile
    assert "docs-build: docs-examples docs-openapi docs-constraints docs-error-codes" in makefile
    assert "docs-serve: docs-examples docs-openapi docs-constraints docs-error-codes" in makefile
    assert "docs-error-codes" in phony.split()


def test_the_generated_error_codes_artefact_is_not_committed():
    """It is built by docs-build like its two siblings; a tracked copy would go stale in review."""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "docs/api/error-codes.json" in gitignore
