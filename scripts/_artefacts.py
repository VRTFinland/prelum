"""Shared plumbing for the published documentation artefacts under docs/api/.

Callers diff these files in their own CI, so their serialisation is part of what Prelum publishes:
the indent, the key order and the trailing newline are all contract. Stated once here rather than
in each exporter, where changing one silently reformatted one artefact and not its three siblings.

Each exporter stays its own module with its own Makefile target, because tests/test_docs.py pins
both as the documentation build's interface.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_DOCS = ROOT / "docs" / "api"


def write_json_artefact(document: object, destination: Path) -> None:
    """
    Write one published artefact, creating its directory if the docs build has not yet run.

    Sorted keys and a fixed indent so a regenerated artefact diffs against the previous one by
    content alone, rather than by whatever order the builder happened to produce.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    _ = destination.write_text(f"{json.dumps(document, indent=2, sort_keys=True)}\n", encoding="utf-8")
