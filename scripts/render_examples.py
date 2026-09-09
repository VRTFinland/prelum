"""Render the site examples from examples/<name>/ into docs/examples/.

Run: make docs-examples

The files under docs/examples are derived artefacts published by the documentation site. Edit the
template or its data under examples/<name>/ and re-run this script; tests/test_examples.py fails
the build when a published output no longer matches the source it was rendered from.

Rendering goes through TypstRenderer rather than a running Prelum instance, so building the
documentation needs the typst binary but not the service.
"""

import asyncio
import base64
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings
from app.models import JSONValue, PdfOutput, PngOutput, RenderFile, RenderJob, RenderOutput, SvgOutput
from app.render.renderer import TypstRenderer
from scripts.manual_content import LOGO_KEY, build_manual_data

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
DESTINATION = ROOT / "docs" / "examples"

# A4 at 110 ppi is 947x1339: large enough to read the layout on the site, small enough that the
# rendered previews stay a fraction of the page weight.
PREVIEW_PPI = 110


@dataclass(frozen=True, slots=True)
class Example:
    name: str
    outputs: tuple[RenderOutput, ...]
    # Files the request carries beside the source, keyed by the path the template imports them by.
    assets: Mapping[str, Path] = field(default_factory=dict)
    # Examples whose data is derived rather than authored supply it here instead of from data.json.
    build_data: Callable[[], JSONValue] | None = None


EXAMPLE_SET = (
    Example(
        name="invoice",
        outputs=(
            PdfOutput(filename="invoice.pdf"),
            PngOutput(filename="invoice.png", ppi=PREVIEW_PPI),
            SvgOutput(filename="invoice.svg"),
        ),
    ),
    Example(
        name="label",
        outputs=(
            PdfOutput(filename="label.pdf"),
            PngOutput(filename="label.png", ppi=PREVIEW_PPI),
            SvgOutput(filename="label.svg"),
        ),
    ),
    Example(
        name="report",
        outputs=(
            PdfOutput(filename="report.pdf"),
            # The report spans three pages, so the direct image outputs name one and the archive
            # carries the rest.
            PngOutput(filename="report.png", page=1, ppi=PREVIEW_PPI),
            SvgOutput(filename="report.svg", page=1),
            PngOutput(filename="report-pages.zip", archive="zip", ppi=PREVIEW_PPI),
        ),
    ),
    Example(
        name="manual",
        outputs=(
            PdfOutput(filename="manual.pdf"),
            PngOutput(filename="manual.png", page=1, ppi=PREVIEW_PPI),
            SvgOutput(filename="manual.svg", page=1),
        ),
        assets={
            LOGO_KEY: ROOT / "docs" / "prelum-logo.svg",
            # Both faces travel with the request: the compiler bundles no modern sans, and a system
            # font would make the manual depend on whichever machine built it.
            **{
                f"fonts/{name}.ttf": EXAMPLES / "manual" / "fonts" / f"{name}.ttf"
                for name in (
                    "SpaceGrotesk-Medium",
                    "SpaceGrotesk-Bold",
                    "InterDisplay-Medium",
                    "InterDisplay-Bold",
                )
            },
        },
        build_data=lambda: build_manual_data(ROOT),
    ),
)


def example_data(example: Example) -> JSONValue:
    if example.build_data is not None:
        return example.build_data()
    data_file = EXAMPLES / example.name / "data.json"
    return json.loads(data_file.read_text(encoding="utf-8")) if data_file.is_file() else {}


# Which suffixes travel as text and which as base64. Guessing from the bytes would quietly
# corrupt a font that happened to decode; an unlisted suffix stops the build instead.
_TEXT_SUFFIXES = frozenset({".svg", ".json", ".typ", ".txt", ".csv"})
_BINARY_SUFFIXES = frozenset({".ttf", ".otf", ".png", ".jpg", ".jpeg"})


def as_render_file(path: Path) -> RenderFile:
    suffix = path.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        return RenderFile(encoding="text", content=path.read_text(encoding="utf-8"))
    if suffix in _BINARY_SUFFIXES:
        return RenderFile(encoding="base64", content=base64.b64encode(path.read_bytes()).decode("ascii"))
    raise ValueError(f"no encoding declared for {path.suffix!r}: add it to render_examples.py")


def example_files(example: Example) -> dict[str, RenderFile]:
    return {key: as_render_file(path) for key, path in example.assets.items()}


async def render_example(renderer: TypstRenderer, example: Example) -> None:
    source = (EXAMPLES / example.name / "main.typ").read_text(encoding="utf-8")
    data = example_data(example)
    files = example_files(example)

    for output in example.outputs:
        result = await renderer.render(RenderJob(source=source, files=files, data=data, output=output))
        _ = (DESTINATION / result.filename).write_bytes(result.bytes)


async def render_all() -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    renderer = TypstRenderer(Settings(environment="test"))

    for example in EXAMPLE_SET:
        await render_example(renderer, example)


if __name__ == "__main__":
    asyncio.run(render_all())
