import json
from pathlib import Path

from app.core.constraints import files_key_rules

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DESTINATION = ROOT / "docs" / "api" / "files-key-rules.json"


def export_constraints(destination: Path = DEFAULT_DESTINATION) -> None:
    """
    Write the deployment-independent files-key rules for the static documentation site.

    Only the half that is a property of the code. A caller reads its limits from GET /v1/constraints;
    publishing defaults here would invite the caller to hardcode them again, which is the duplication
    this artefact exists to remove.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = json.dumps(files_key_rules(), indent=2, sort_keys=True)
    _ = destination.write_text(f"{document}\n", encoding="utf-8")


if __name__ == "__main__":
    export_constraints()
