import json
from pathlib import Path

from app.core.output_rules import output_rules

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DESTINATION = ROOT / "docs" / "api" / "output-rules.json"


def export_output_rules(destination: Path = DEFAULT_DESTINATION) -> None:
    """
    Write the output-option rules and their conformance vectors for the static documentation site.

    The whole document is a property of the code, so unlike the files-key rules there is no half to
    withhold: a caller needs nothing from a running deployment to validate an `output` object, and
    the vectors are what lets it prove its validator agrees with ours rather than transcribing it.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = json.dumps(output_rules(), indent=2, sort_keys=True)
    _ = destination.write_text(f"{document}\n", encoding="utf-8")


if __name__ == "__main__":
    export_output_rules()
