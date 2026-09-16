from pathlib import Path

from app.core.constraints import files_key_rules
from scripts._artefacts import API_DOCS, write_json_artefact

DEFAULT_DESTINATION = API_DOCS / "files-key-rules.json"


def export_constraints(destination: Path = DEFAULT_DESTINATION) -> None:
    """
    Write the deployment-independent files-key rules for the static documentation site.

    Only the half that is a property of the code. A caller reads its limits from GET /v1/constraints;
    publishing defaults here would invite the caller to hardcode them again, which is the duplication
    this artefact exists to remove.
    """
    write_json_artefact(files_key_rules(), destination)


if __name__ == "__main__":
    export_constraints()
