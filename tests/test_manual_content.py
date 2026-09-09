"""The manual is generated from the documentation, so the converter is the thing that can lie."""

from pathlib import Path

import pytest

from scripts.manual_content import (
    EXCLUDED_PAGES,
    LOGO_KEY,
    ROOT,
    UnsupportedMarkdownError,
    build_manual_data,
    chapter_id,
    nav_pages,
    parse_markdown,
    resolve_links,
)


def test_headings_carry_their_level():
    blocks = parse_markdown("# Getting started\n\n## Requirements\n")

    assert blocks == [
        {"type": "heading", "level": 1, "text": "Getting started"},
        {"type": "heading", "level": 2, "text": "Requirements"},
    ]


def test_wrapped_paragraph_lines_become_one_paragraph():
    blocks = parse_markdown("Local development requires\nPython 3.14 or later.\n")

    assert blocks == [
        {"type": "paragraph", "spans": [{"kind": "text", "text": "Local development requires Python 3.14 or later."}]}
    ]


def test_blank_line_separates_paragraphs():
    blocks = parse_markdown("First.\n\nSecond.\n")

    assert [block["type"] for block in blocks] == ["paragraph", "paragraph"]


def test_fenced_code_keeps_its_language_and_body():
    blocks = parse_markdown('```bash\nuv sync\necho "done"\n```\n')

    assert blocks == [{"type": "code", "language": "bash", "text": 'uv sync\necho "done"'}]


def test_fenced_code_without_a_language_is_untagged():
    blocks = parse_markdown("```\nplain\n```\n")

    assert blocks == [{"type": "code", "language": "", "text": "plain"}]


def test_markdown_inside_a_fence_is_not_parsed():
    blocks = parse_markdown("```\n# not a heading\n| not | a table |\n```\n")

    assert blocks == [{"type": "code", "language": "", "text": "# not a heading\n| not | a table |"}]


def test_table_separates_header_from_rows():
    source = "| Endpoint | Purpose |\n| --- | --- |\n| `/health` | Liveness |\n"

    blocks = parse_markdown(source)

    assert blocks == [
        {
            "type": "table",
            "header": [
                [{"kind": "text", "text": "Endpoint"}],
                [{"kind": "text", "text": "Purpose"}],
            ],
            "rows": [
                [
                    [{"kind": "code", "text": "/health"}],
                    [{"kind": "text", "text": "Liveness"}],
                ]
            ],
        }
    ]


def test_bullet_list_items_are_spans():
    blocks = parse_markdown("- First item\n- Second `item`\n")

    assert blocks == [
        {
            "type": "list",
            "items": [
                [{"kind": "text", "text": "First item"}],
                [{"kind": "text", "text": "Second "}, {"kind": "code", "text": "item"}],
            ],
        }
    ]


def test_inline_code_bold_and_links_become_spans():
    blocks = parse_markdown("Set `PRELUM_API_TOKEN` or **fail**, see [uv](https://docs.astral.sh/uv/).\n")

    assert blocks == [
        {
            "type": "paragraph",
            "spans": [
                {"kind": "text", "text": "Set "},
                {"kind": "code", "text": "PRELUM_API_TOKEN"},
                {"kind": "text", "text": " or "},
                {"kind": "strong", "text": "fail"},
                {"kind": "text", "text": ", see "},
                {"kind": "link", "text": "uv", "href": "https://docs.astral.sh/uv/"},
                {"kind": "text", "text": "."},
            ],
        }
    ]


@pytest.mark.parametrize(
    "source",
    [
        "!!! note\n    An admonition.\n",
        "> A block quote.\n",
        "1. A numbered item\n",
        '<section class="hero">\n',
        '--8<-- "examples/invoice/main.typ"\n',
    ],
)
def test_unsupported_constructs_are_refused(source: str):
    """A construct the converter cannot render must stop the build, never vanish from the manual.

    A silently dropped section produces a manual that looks complete and is not, which is worse
    than no manual at all.
    """
    with pytest.raises(UnsupportedMarkdownError):
        _ = parse_markdown(source)


def test_an_unterminated_fence_is_refused():
    with pytest.raises(UnsupportedMarkdownError):
        _ = parse_markdown("```bash\nuv sync\n")


def test_nav_pages_reports_section_title_and_path_in_navigation_order(tmp_path: Path):
    config = tmp_path / "mkdocs.yml"
    _ = config.write_text(
        "nav:\n"
        "  - Home: index.md\n"
        "  - API:\n"
        "      - Render request: api/render.md\n"
        "      - Errors: api/errors.md\n"
        "  - Contributing: https://example.com/CONTRIBUTING.md\n",
        encoding="utf-8",
    )

    assert nav_pages(config) == [
        (None, "Home", "index.md"),
        ("API", "Render request", "api/render.md"),
        ("API", "Errors", "api/errors.md"),
    ]


def test_every_published_documentation_page_converts():
    """The real pages are the input that matters; a construct added to them must fail here first."""
    pages = [page for _, _, page in nav_pages(ROOT / "mkdocs.yml") if page not in EXCLUDED_PAGES]

    assert pages, "navigation produced no convertible pages"
    for page in pages:
        blocks = parse_markdown((ROOT / "docs" / page).read_text(encoding="utf-8"))
        assert blocks, f"{page} converted to nothing"


def test_manual_data_carries_every_published_page_in_navigation_order():
    data = build_manual_data(ROOT)

    expected = [title for _, title, page in nav_pages(ROOT / "mkdocs.yml") if page not in EXCLUDED_PAGES]
    assert [chapter["title"] for chapter in data["chapters"]] == expected
    assert data["title"] == "Prelum"
    assert data["logo"] == LOGO_KEY
    assert all(chapter["blocks"] for chapter in data["chapters"])


def test_a_link_to_another_manual_page_becomes_an_internal_reference():
    blocks = resolve_links(
        parse_markdown("See [Output formats](output-formats.md) for details.\n"),
        page="api/render.md",
        pages={"api/output-formats.md"},
        site_url="https://example.test/prelum/",
    )

    assert blocks[0]["spans"][1] == {
        "kind": "ref",
        "text": "Output formats",
        "target": chapter_id("api/output-formats.md"),
    }


def test_a_relative_link_resolves_against_its_own_page():
    blocks = resolve_links(
        parse_markdown("See [Docker](operations/docker.md).\n"),
        page="getting-started.md",
        pages={"operations/docker.md"},
        site_url="https://example.test/prelum/",
    )

    assert blocks[0]["spans"][1]["target"] == chapter_id("operations/docker.md")


def test_an_anchor_still_reaches_the_chapter():
    blocks = resolve_links(
        parse_markdown("See [PPI](api/output-formats.md#direct-png-and-svg).\n"),
        page="index.md",
        pages={"api/output-formats.md"},
        site_url="https://example.test/prelum/",
    )

    assert blocks[0]["spans"][1]["target"] == chapter_id("api/output-formats.md")


def test_a_link_to_a_page_the_manual_omits_points_at_the_site():
    blocks = resolve_links(
        parse_markdown("See [Examples](examples.md).\n"),
        page="getting-started.md",
        pages=set(),
        site_url="https://example.test/prelum/",
    )

    assert blocks[0]["spans"][1] == {
        "kind": "link",
        "text": "Examples",
        "href": "https://example.test/prelum/examples/",
    }


def test_an_external_link_is_left_alone():
    blocks = resolve_links(
        parse_markdown("See [uv](https://docs.astral.sh/uv/).\n"),
        page="getting-started.md",
        pages=set(),
        site_url="https://example.test/prelum/",
    )

    assert blocks[0]["spans"][1] == {"kind": "link", "text": "uv", "href": "https://docs.astral.sh/uv/"}


def test_links_inside_lists_and_tables_are_resolved_too():
    source = "- See [Errors](api/errors.md)\n\n| Page |\n| --- |\n| [Errors](api/errors.md) |\n"

    blocks = resolve_links(
        parse_markdown(source),
        page="index.md",
        pages={"api/errors.md"},
        site_url="https://example.test/prelum/",
    )

    assert blocks[0]["items"][0][1]["kind"] == "ref"
    assert blocks[1]["rows"][0][0][0]["kind"] == "ref"


def test_every_manual_link_resolves_to_a_chapter_or_an_absolute_url():
    """No link in the manual may be a relative path: a PDF has nothing to resolve it against."""
    data = build_manual_data(ROOT)
    ids = {chapter["id"] for chapter in data["chapters"]}

    def check(spans: list[dict[str, str]]) -> None:
        for span in spans:
            if span["kind"] == "ref":
                assert span["target"] in ids
            elif span["kind"] == "link":
                assert span["href"].startswith("http"), span

    for chapter in data["chapters"]:
        for block in chapter["blocks"]:
            if block["type"] == "paragraph":
                check(block["spans"])
            elif block["type"] == "list":
                for item in block["items"]:
                    check(item)
            elif block["type"] == "table":
                for row in [block["header"], *block["rows"]]:
                    for cell in row:
                        check(cell)


def test_a_link_to_a_published_file_keeps_its_name():
    """Only .md targets are pages; an asset link must not be turned into a directory URL."""
    blocks = resolve_links(
        parse_markdown("Download [the schema](openapi.json).\n"),
        page="api/openapi.md",
        pages=set(),
        site_url="https://example.test/prelum/",
    )

    assert blocks[0]["spans"][1]["href"] == "https://example.test/prelum/api/openapi.json"
