from pathlib import Path

from app.core.output_rules import output_rules
from scripts._artefacts import API_DOCS, write_json_artefact

DEFAULT_DESTINATION = API_DOCS / "output-rules.json"


def export_output_rules(destination: Path = DEFAULT_DESTINATION) -> None:
    """
    Write the output-option rules and their conformance vectors for the static documentation site.

    The whole document is a property of the code, so unlike the files-key rules there is no half to
    withhold: a caller needs nothing from a running deployment to validate an `output` object, and
    the vectors are what lets it prove its validator agrees with ours rather than transcribing it.
    """
    write_json_artefact(output_rules(), destination)


if __name__ == "__main__":
    export_output_rules()
