"""Convert the documentation Markdown into the block list the manual template typesets.

The manual is a rendering of the published documentation, not a second copy of it, so this module
owns the one translation between them. It accepts the constructs `docs/` actually uses and refuses
everything else: a construct that were merely skipped would leave a manual that looks complete and
silently is not.
"""

import re
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

# index.md is a hand-built HTML hero and examples.md embeds the rendered examples themselves; both
# describe the site rather than the service, and neither belongs in a printed manual.
EXCLUDED_PAGES = frozenset({"index.md", "examples.md"})

# The project-relative key the manual imports the logo by; the request carries the file under
# this name in `files`, so the template resolves it exactly as it would any other asset.
LOGO_KEY = "assets/prelum-logo.svg"

type Span = dict[str, str]
type Block = dict[str, Any]


class UnsupportedMarkdownError(RuntimeError):
    """Raised for a construct the manual template cannot typeset."""


_HEADING = re.compile(r"^(#+) +(.+?)\s*$")
_FENCE = re.compile(r"^```(\S*)\s*$")
_LIST_ITEM = re.compile(r"^- +(.+?)\s*$")
_TABLE_DIVIDER = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
_NUMBERED_ITEM = re.compile(r"^\d+\. ")
_INLINE = re.compile(r"`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)]+)\)")

_MAX_HEADING_LEVEL = 3

# Every construct the site uses that the manual deliberately does not carry. Naming them keeps the
# failure message useful: the build says which construct and which line, not merely "unsupported".
_REFUSED = (
    ("!!!", "admonition"),
    ("???", "collapsible block"),
    ("===", "content tab"),
    ("--8<--", "snippet include"),
    (">", "block quote"),
    ("<", "raw HTML"),
    ("---", "thematic break or front matter"),
)


def _refuse(line: str, number: int, construct: str) -> UnsupportedMarkdownError:
    return UnsupportedMarkdownError(f"line {number}: unsupported {construct}: {line.strip()[:60]}")


def parse_inline(text: str) -> list[Span]:
    """Split one line of Markdown into text, inline code, bold and link spans."""
    spans: list[Span] = []
    position = 0
    for match in _INLINE.finditer(text):
        if match.start() > position:
            spans.append({"kind": "text", "text": text[position : match.start()]})
        code, strong, label, href = match.groups()
        if code is not None:
            spans.append({"kind": "code", "text": code})
        elif strong is not None:
            spans.append({"kind": "strong", "text": strong})
        else:
            spans.append({"kind": "link", "text": label, "href": href})
        position = match.end()
    if position < len(text):
        spans.append({"kind": "text", "text": text[position:]})
    return spans


def _table_cells(line: str) -> list[list[Span]]:
    return [parse_inline(cell.strip()) for cell in line.strip().strip("|").split("|")]


def parse_markdown(text: str) -> list[Block]:
    """Convert one documentation page into blocks, refusing anything it cannot represent."""
    lines = text.splitlines()
    blocks: list[Block] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        number = index + 1

        if not line.strip():
            index += 1
            continue

        if (fence := _FENCE.match(line)) is not None:
            body: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                body.append(lines[index])
                index += 1
            if index == len(lines):
                raise UnsupportedMarkdownError(f"line {number}: unterminated code fence")
            blocks.append({"type": "code", "language": fence.group(1), "text": "\n".join(body)})
            index += 1
            continue

        if (heading := _HEADING.match(line)) is not None:
            level = len(heading.group(1))
            if level > _MAX_HEADING_LEVEL:
                raise _refuse(line, number, f"heading level {level}")
            blocks.append({"type": "heading", "level": level, "text": heading.group(2)})
            index += 1
            continue

        if line.startswith("|"):
            if index + 1 >= len(lines) or _TABLE_DIVIDER.match(lines[index + 1]) is None:
                raise _refuse(line, number, "table without a header divider")
            header = _table_cells(line)
            index += 2
            rows: list[list[list[Span]]] = []
            while index < len(lines) and lines[index].startswith("|"):
                rows.append(_table_cells(lines[index]))
                index += 1
            blocks.append({"type": "table", "header": header, "rows": rows})
            continue

        if _LIST_ITEM.match(line) is not None:
            items: list[list[Span]] = []
            while index < len(lines) and (item := _LIST_ITEM.match(lines[index])) is not None:
                items.append(parse_inline(item.group(1)))
                index += 1
            blocks.append({"type": "list", "items": items})
            continue

        for prefix, construct in _REFUSED:
            if line.startswith(prefix):
                raise _refuse(line, number, construct)
        if _NUMBERED_ITEM.match(line) is not None:
            raise _refuse(line, number, "numbered list")

        paragraph: list[str] = []
        while index < len(lines) and lines[index].strip() and not _starts_a_block(lines[index]):
            paragraph.append(lines[index].strip())
            index += 1
        blocks.append({"type": "paragraph", "spans": parse_inline(" ".join(paragraph))})

    return blocks


def _starts_a_block(line: str) -> bool:
    """Whether this line ends the paragraph being gathered rather than continuing it."""
    return (
        _HEADING.match(line) is not None
        or _FENCE.match(line) is not None
        or _LIST_ITEM.match(line) is not None
        or line.startswith("|")
        or any(line.startswith(prefix) for prefix, _ in _REFUSED)
    )


def nav_pages(mkdocs_config: Path) -> list[tuple[str | None, str, str]]:
    """The published pages, in navigation order, as (section, title, path) triples.

    Reading the order from `mkdocs.yml` rather than restating it keeps the manual's contents from
    drifting out of step with the site's own navigation. External navigation entries are not pages
    and are skipped.
    """
    config = yaml.safe_load(mkdocs_config.read_text(encoding="utf-8"))
    pages: list[tuple[str | None, str, str]] = []

    def walk(entries: list[Any], section: str | None) -> None:
        for entry in entries:
            for title, target in entry.items():
                if isinstance(target, list):
                    walk(target, title)
                elif isinstance(target, str) and target.endswith(".md") and "://" not in target:
                    pages.append((section, title, target))

    walk(config["nav"], None)
    return pages


def chapter_id(page: str) -> str:
    """A stable Typst label for one chapter, derived from its documentation path."""
    return "ch-" + page.removesuffix(".md").replace("/", "-")


def _site_href(target: str, site_url: str) -> str:
    """The published URL of a documentation target the manual itself cannot satisfy.

    Only `.md` targets are pages, and MkDocs publishes those as directories. Anything else is a
    file it copies verbatim, so appending a directory slash would break the link.
    """
    base = site_url.rstrip("/")
    if not target.endswith(".md"):
        return f"{base}/{target}"
    return f"{base}/{target.removesuffix('.md').removesuffix('index')}".rstrip("/") + "/"


def _resolve_span(span: Span, page: str, pages: set[str], site_url: str) -> Span:
    if span["kind"] != "link":
        return span
    href = span["href"]
    if "://" in href or href.startswith("mailto:"):
        return span

    # Documentation links are relative to the page that carries them, exactly as MkDocs resolves
    # them; a PDF has no such base, so every one of them has to become a label or an absolute URL.
    target, _, _anchor = href.partition("#")
    if not target:
        return span
    resolved = str(PurePosixPath(PurePosixPath(page).parent / target)).removeprefix("./")

    if resolved in pages:
        return {"kind": "ref", "text": span["text"], "target": chapter_id(resolved)}
    return {"kind": "link", "text": span["text"], "href": _site_href(resolved, site_url)}


def resolve_links(blocks: list[Block], *, page: str, pages: set[str], site_url: str) -> list[Block]:
    """Turn every documentation-relative link into a chapter reference or an absolute URL."""

    def spans(items: list[Span]) -> list[Span]:
        return [_resolve_span(span, page, pages, site_url) for span in items]

    def cells(row: list[list[Span]]) -> list[list[Span]]:
        return [spans(cell) for cell in row]

    resolved: list[Block] = []
    for block in blocks:
        if block["type"] == "paragraph":
            resolved.append({**block, "spans": spans(block["spans"])})
        elif block["type"] == "list":
            resolved.append({**block, "items": [spans(item) for item in block["items"]]})
        elif block["type"] == "table":
            resolved.append(
                {
                    **block,
                    "header": cells(block["header"]),
                    "rows": [cells(row) for row in block["rows"]],
                }
            )
        else:
            resolved.append(block)
    return resolved


def build_manual_data(root: Path) -> dict[str, Any]:
    """Assemble the manual's `data` payload from the site configuration and its published pages."""
    config = yaml.safe_load((root / "mkdocs.yml").read_text(encoding="utf-8"))
    docs = root / "docs"
    pages = [entry for entry in nav_pages(root / "mkdocs.yml") if entry[2] not in EXCLUDED_PAGES]
    included = {page for _, _, page in pages}

    return {
        "title": config["site_name"],
        "subtitle": "Typst-based render API",
        "description": config["site_description"],
        "site_url": config["site_url"],
        "repo_url": config["repo_url"],
        "logo": LOGO_KEY,
        "chapters": [
            {
                "id": chapter_id(page),
                "section": section,
                "title": title,
                "blocks": resolve_links(
                    parse_markdown((docs / page).read_text(encoding="utf-8")),
                    page=page,
                    pages=included,
                    site_url=config["site_url"],
                ),
            }
            for section, title, page in pages
        ],
    }
