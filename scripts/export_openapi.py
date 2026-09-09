import json
from pathlib import Path

from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DESTINATION = ROOT / "docs" / "api" / "openapi.json"


def export_openapi(destination: Path = DEFAULT_DESTINATION) -> None:
    """Write the application's generated OpenAPI schema for the static documentation site."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    schema = create_app().openapi()
    _ = destination.write_text(f"{json.dumps(schema, indent=2, sort_keys=True)}\n", encoding="utf-8")


if __name__ == "__main__":
    export_openapi()
