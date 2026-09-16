from pathlib import Path

from app.main import create_app
from scripts._artefacts import API_DOCS, write_json_artefact

DEFAULT_DESTINATION = API_DOCS / "openapi.json"


def export_openapi(destination: Path = DEFAULT_DESTINATION) -> None:
    """Write the application's generated OpenAPI schema for the static documentation site."""
    write_json_artefact(create_app().openapi(), destination)


if __name__ == "__main__":
    export_openapi()
