"""Regenerate examples/render-request.json from the .typ files it must never drift from.

Run: make regenerate-examples

examples/render-request.json is a derived artefact, not a hand-maintained one: it is the JSON
rendering of examples/hello.typ and examples/lib/label.typ. Edit those two files and re-run this
script rather than editing the JSON directly -- tests/test_examples.py fails the build if the JSON
and the .typ files ever disagree.
"""

import json
from pathlib import Path

from scripts.render_examples import as_render_file

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def main() -> None:
    body = {
        "source": (EXAMPLES / "hello.typ").read_text(encoding="utf-8"),
        # Encoded by the same rule as every other example asset rather than by a second one written
        # here: as_render_file decides text or base64 from the suffix, because guessing from the
        # bytes would quietly corrupt an asset that happened to decode. Adding a binary file to this
        # request then needs no decision at all.
        "files": {"lib/label.typ": as_render_file(EXAMPLES / "lib" / "label.typ").model_dump()},
        "data": {"name": "World"},
        "output": {"format": "pdf", "filename": "hello.pdf"},
    }
    (EXAMPLES / "render-request.json").write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
