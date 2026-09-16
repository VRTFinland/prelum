from pathlib import Path

from app.core.errors import error_codes
from scripts._artefacts import API_DOCS, write_json_artefact

DEFAULT_DESTINATION = API_DOCS / "error-codes.json"


def export_error_codes(destination: Path = DEFAULT_DESTINATION) -> None:
    """
    Write the code, status and origin of every error for the static documentation site.

    A caller that wants to pin its branch on `origin` can diff this artefact instead of transcribing
    the table by hand, which is the duplication the field exists to remove.
    """
    write_json_artefact(error_codes(), destination)


if __name__ == "__main__":
    export_error_codes()
