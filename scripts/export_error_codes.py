import json
from pathlib import Path

from app.core.errors import error_codes

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DESTINATION = ROOT / "docs" / "api" / "error-codes.json"


def export_error_codes(destination: Path = DEFAULT_DESTINATION) -> None:
    """
    Write the code, status and origin of every error for the static documentation site.

    A caller that wants to pin its branch on `origin` can diff this artefact instead of transcribing
    the table by hand, which is the duplication the field exists to remove.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = json.dumps(error_codes(), indent=2, sort_keys=True)
    _ = destination.write_text(f"{document}\n", encoding="utf-8")


if __name__ == "__main__":
    export_error_codes()
