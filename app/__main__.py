"""Console entry point, so the listen address has exactly one owner: Settings."""

import uvicorn

from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.bind, port=settings.port)


if __name__ == "__main__":
    main()
