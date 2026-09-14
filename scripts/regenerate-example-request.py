"""Regenerate examples/render-request.json from the .typ files it must never drift from.

Run: uv run python scripts/regenerate-example-request.py

examples/render-request.json is a derived artefact, not a hand-maintained one: it is the JSON
rendering of examples/hello.typ and examples/lib/label.typ. Edit those two files and re-run this
script rather than editing the JSON directly -- tests/test_examples.py fails the build if the JSON
and the .typ files ever disagree.
"""

import json
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def main() -> None:
    body = {
        "source": (EXAMPLES / "hello.typ").read_text(encoding="utf-8"),
        "files": {
            "lib/label.typ": {
                "encoding": "text",
                "content": (EXAMPLES / "lib" / "label.typ").read_text(encoding="utf-8"),
            }
        },
        "data": {"name": "World"},
        "output": {"format": "pdf", "filename": "hello.pdf"},
    }
    (EXAMPLES / "render-request.json").write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
