"""The published output-option rules must agree with the models that enforce them.

A conformance vector that disagrees with the service is worse than no vector: callers run these in
their own CI, so a wrong one propagates the drift it exists to prevent, wearing the authority of the
service. The same reasoning as tests/test_constraints.py, one layer over.
"""

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.output_rules import CONFORMANCE_VECTORS, OUTPUT_RULES_VERSION, RULES, RULES_WITHOUT_VECTORS, output_rules
from app.main import app
from app.models import (
    DEFAULT_PNG_PPI,
    MAX_PAGE_SELECTION_LENGTH,
    MAX_PAGE_SELECTION_SEGMENTS,
    MAX_PDF_STANDARDS,
    MAX_PNG_PPI,
    MIN_IMAGE_PAGE,
    MIN_PNG_PPI,
    PAGE_SELECTION_PATTERN,
    PDF_A_4_STANDARDS,
    PDF_A_VERSION,
    TAGGED_PDF_STANDARDS,
    OutputFormat,
    OutputRuleId,
    PdfStandard,
    PdfVersion,
    RenderRequest,
)

client = TestClient(app)
TOKEN = {"X-Prelum-Api-Token": "dev-only-insecure-token"}
SOURCE = '#text("hello")'


def _vector_id(vector: dict[str, Any]) -> str:
    return vector.get("rule") or vector.get("code") or "accepted"


@pytest.mark.parametrize("vector", CONFORMANCE_VECTORS, ids=_vector_id)
def test_every_conformance_vector_matches_the_service(vector: dict[str, Any]):
    """
    Accepted vectors are validated, rejected ones are sent.

    An accepted vector cannot be posted: it would reach the renderer and fork typst, so it is proved
    where the decision is actually made. A rejected one never gets that far, so it goes over HTTP —
    the published `code` and `context.rule` are what a caller reads, and asserting them against the
    real response is the only way this test cannot agree with a mistake in the error handler.
    """
    if vector["accepted"]:
        _ = RenderRequest.model_validate({"source": SOURCE, "output": vector["output"]})
        return

    response = client.post("/v1/render", json={"source": SOURCE, "output": vector["output"]}, headers=TOKEN)

    assert response.status_code == 400
    context = response.json()["context"]
    assert response.json()["code"] == vector["code"]
    if "rule" in vector:
        assert context["rule"] == vector["rule"]
    else:
        # A field-level rejection must not quietly carry an id the document does not mention: the
        # artefact would then understate what the service tells a caller.
        assert "rule" not in context


def test_every_rule_has_a_vector_or_a_declared_exemption():
    covered = {vector["rule"] for vector in CONFORMANCE_VECTORS if "rule" in vector}

    for rule in RULES:
        assert rule["id"] in covered or rule["id"] in RULES_WITHOUT_VECTORS, (
            f"output rule {rule['id']} has neither a conformance vector nor a declared exemption"
        )


def test_declared_exemptions_name_real_rules():
    unknown = RULES_WITHOUT_VECTORS - {rule["id"] for rule in RULES}

    assert not unknown, f"RULES_WITHOUT_VECTORS exempts rules that do not exist: {sorted(unknown)}"


def test_the_document_publishes_exactly_the_rules_models_enforces():
    """
    A rule here and not in models.py is one Prelum does not enforce; the reverse is one it hides.

    Both directions are the same defect as a files-key rule published without a validator behind it:
    a mirror that refuses what the service renders, or renders what the service refuses.
    """
    published = [rule["id"] for rule in RULES]

    assert sorted(published) == sorted(rule.value for rule in OutputRuleId)
    assert len(published) == len(set(published))


def test_document_states_the_enforced_limits_and_tables():
    document = output_rules()

    assert document["output_rules_version"] == OUTPUT_RULES_VERSION
    assert document["formats"] == [output_format.value for output_format in OutputFormat]
    assert document["pdf"]["versions"] == [version.value for version in PdfVersion]
    assert document["pdf"]["standards"] == [standard.value for standard in PdfStandard]
    assert document["pdf"]["max_standards"] == MAX_PDF_STANDARDS
    assert document["pdf"]["pdf_a_version"] == {
        standard.value: version.value for standard, version in PDF_A_VERSION.items()
    }
    assert document["pdf"]["tagged_standards"] == sorted(standard.value for standard in TAGGED_PDF_STANDARDS)
    assert document["pdf"]["pdf_a_4_standards"] == sorted(standard.value for standard in PDF_A_4_STANDARDS)
    assert document["image"]["min_page"] == MIN_IMAGE_PAGE
    assert document["image"]["png"] == {
        "min_ppi": MIN_PNG_PPI,
        "max_ppi": MAX_PNG_PPI,
        "default_ppi": DEFAULT_PNG_PPI,
    }
    assert document["page_selection"]["max_length"] == MAX_PAGE_SELECTION_LENGTH
    assert document["page_selection"]["max_selections"] == MAX_PAGE_SELECTION_SEGMENTS
    assert document["page_selection"]["selection_pattern"] == PAGE_SELECTION_PATTERN


def test_the_static_document_carries_no_deployment_configuration():
    """The output rules are a property of the code; a limit here would invite a caller to pin it."""
    document = output_rules()

    assert "limits" not in document
    assert not {"max_output_files", "max_output_bytes", "font_path", "environment"} & document.keys()


def _mirror(document: dict[str, Any], output: dict[str, Any]) -> str | None:
    """
    Decide one `output` object using nothing but the published document.

    This is the client the artefact exists for, written the way a caller would have to write it: the
    tables come from `pdf`, the grammar from `page_selection`, and the rules are applied in the order
    `rules` lists them, which `rule_evaluation` promises is the order Prelum uses. Nothing is
    imported from app.models, so a rule that reaches the validators without reaching the document
    fails here rather than in a caller's deployment.

    The literals it does spell — `ua-1`, `2.0` — are named by the rules that use them, the same way
    the files-key set rules are implemented from their descriptions. What a mirror cannot derive is
    published as data, and that is what this proves.
    """
    selection = document["page_selection"]
    pdf_a_version: dict[str, str] = document["pdf"]["pdf_a_version"]
    pages = output.get("pages")

    if pages is not None:
        if len(pages) > selection["max_length"]:
            return "page_selection_too_long"
        parts = pages.split(",")
        if len(parts) > selection["max_selections"]:
            return "page_selection_too_many_segments"
        pattern = re.compile(selection["selection_pattern"])
        if any(pattern.fullmatch(part) is None for part in parts):
            return "page_selection_malformed"
        for part in parts:
            start, _, end = part.partition("-")
            if end and int(end) < int(start):
                return "page_range_end_precedes_start"

    if output["format"] == "pdf":
        standards: list[str] = output.get("standards", [])
        if len(set(standards)) != len(standards):
            return "duplicate_standards"
        pdf_a = [standard for standard in standards if standard in pdf_a_version]
        if len(pdf_a) > 1:
            return "multiple_pdf_a_standards"
        if "ua-1" in standards and set(document["pdf"]["pdf_a_4_standards"]).intersection(pdf_a):
            return "ua_1_with_pdf_a_4"
        version = output.get("version")
        if version is not None and pdf_a and version != pdf_a_version[pdf_a[0]]:
            return "version_conflicts_with_standard"
        if version == "2.0" and "ua-1" in standards:
            return "ua_1_with_pdf_2_0"
        if pages is not None and set(document["pdf"]["tagged_standards"]).intersection(standards):
            return "pages_with_tagged_standard"
        return None

    if output.get("archive") is None and pages is not None:
        return "pages_requires_archive"
    if output.get("archive") is not None and output.get("page") is not None:
        return "page_with_archive"
    return None


@pytest.mark.parametrize(
    "vector",
    [vector for vector in CONFORMANCE_VECTORS if vector["accepted"] or "rule" in vector],
    ids=_vector_id,
)
def test_a_mirror_built_from_the_document_alone_agrees_with_the_service(vector: dict[str, Any]):
    """
    The point of the artefact: a client-side validator that is tested rather than transcribed.

    Only the vectors the document is responsible for. A field-level rejection — an unknown field, a
    ppi out of range, a format that does not exist — is stated by the OpenAPI schema, and a mirror
    reads it from there; asking this one to answer them would be asking the document to publish the
    request schema a second time.
    """
    document = output_rules()

    assert _mirror(document, vector["output"]) == vector.get("rule")


def test_the_mirror_is_not_vacuous():
    """A mirror that returned None for everything would pass every accepted vector above."""
    document = output_rules()

    assert _mirror(document, {"format": "pdf", "standards": ["a-2b", "a-3b"]}) == "multiple_pdf_a_standards"
    assert _mirror(document, {"format": "png", "pages": "1-2"}) == "pages_requires_archive"


def test_rejected_vectors_are_rejected_by_the_models_too():
    """
    The HTTP assertions above would hold even if the route, not validation, did the rejecting.

    Every rejected vector must fail at the model, which is where the rule lives — otherwise a
    caller's pre-flight check cannot reproduce the decision without sending the request.
    """
    for vector in CONFORMANCE_VECTORS:
        if vector["accepted"]:
            continue
        with pytest.raises(ValidationError):
            _ = RenderRequest.model_validate({"source": SOURCE, "output": vector["output"]})
