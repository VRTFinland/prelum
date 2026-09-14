# Choose an output format

Put an `output` object in the [render request](render.md) to choose what Prelum returns. If you
omit it, Prelum returns a PDF with default settings.

| You need | Choose |
| --- | --- |
| A printable or shareable document | `{"format": "pdf"}` |
| One page as a bitmap | `{"format": "png", "page": 1}` |
| One page as vector artwork | `{"format": "svg", "page": 1}` |
| Every page as separate images | PNG or SVG with `"archive": "zip"` |

The examples on this page show only the value of the request's `output` field.

## PDF

For most documents, this is all you need:

```json
{
  "format": "pdf",
  "filename": "report.pdf"
}
```

The filename is optional, and PDF 1.7 is the default. Add advanced options only when the receiving
system has a specific requirement:

```json
{
  "format": "pdf",
  "version": "1.7",
  "standards": ["a-2b"],
  "pages": "1,3-6,8-",
  "filename": "report.pdf"
}
```

`version` accepts `1.4`, `1.5`, `1.6`, `1.7` or `2.0`. When omitted, Typst uses PDF 1.7
unless a conformance standard requires another version.

`standards` accepts one PDF/A profile and an optional compatible `ua-1`. Supported PDF/A
profiles are `a-1b`, `a-1a`, `a-2b`, `a-2u`, `a-2a`, `a-3b`, `a-3u`, `a-3a`, `a-4`,
`a-4f` and `a-4e`. Prelum rejects incompatible versions and combinations.

Use `pages` to return only selected physical pages. Page numbers start at one. Separate selections
with commas and use a hyphen for a range: `1`, `2-5` and `8-` are valid examples. The value is
limited to 256 characters and 64 selections.

Typst disables PDF tagging when pages are selected, so `pages` cannot be combined with `ua-1`,
`a-1a`, `a-2a` or `a-3a`.

## One PNG or SVG

Choose a page when rendering a document as one image:

```json
{
  "format": "png",
  "page": 2,
  "ppi": 144,
  "filename": "page-2.png"
}
```

`page` is a positive physical page number. PNG also accepts `ppi` from 1 to 300 and defaults to
144; a larger value produces more pixels and usually a larger response. SVG does not use `ppi`.

Direct PNG and SVG output can contain only one image. If a document has several pages and `page`
is omitted, Typst cannot write them to the one output file and the request fails with
`template_compile_failed`.

## Multiple PNG or SVG pages

Ask for a ZIP when you need several pages:

```json
{
  "format": "png",
  "archive": "zip",
  "pages": "1,3-6,8-",
  "ppi": 144,
  "filename": "report-pages.zip"
}
```

`archive: "zip"` always returns a ZIP, even if only one page matches. Omit `pages` to include all
pages within the deployment's configured limit. In archive mode, use `pages` rather than the
single-page `page` field; conversely, `pages` is not accepted without archive mode.

ZIP entries are ordered by physical page number and named `page-01.png` or `page-01.svg`. Prelum
rejects the request rather than returning a partial archive when the document has more pages than
`PRELUM_MAX_OUTPUT_FILES` permits.

Both the uncompressed total and the completed ZIP must fit `PRELUM_MAX_OUTPUT_BYTES`. ZIP responses
use `application/zip`, `Content-Disposition: attachment` and `Cache-Control: no-store`.
