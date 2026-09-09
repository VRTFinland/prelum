# Output formats

## PDF

```json
{
  "format": "pdf",
  "version": "1.7",
  "standards": ["a-2b"],
  "pages": "1,3-6,8-",
  "filename": "report.pdf"
}
```

`version` accepts `1.4`, `1.5`, `1.6`, `1.7` or `2.0`. When omitted, Typst uses PDF 1.7 unless a
conformance standard requires another version.

`standards` accepts one PDF/A profile and an optional compatible `ua-1`. Supported PDF/A profiles
are `a-1b`, `a-1a`, `a-2b`, `a-2u`, `a-2a`, `a-3b`, `a-3u`, `a-3a`, `a-4`, `a-4f` and `a-4e`.
Incompatible versions and combinations are rejected.

`pages` uses one-indexed physical page numbers, ASCII digits and comma-separated selections such as
`1`, `2-5` and `8-`. It is limited to 256 characters and 64 entries. Typst disables PDF tagging
when pages are selected, so `pages` cannot be combined with `ua-1`, `a-1a`, `a-2a` or `a-3a`.

## Direct PNG and SVG

```json
{
  "format": "png",
  "page": 2,
  "ppi": 144,
  "filename": "page-2.png"
}
```

PNG accepts an integer `ppi` from 1 to 300 and defaults to 144. Low values can create thumbnails;
an A4 page at 10 PPI is 83 × 117 pixels. SVG has no `ppi` option. Both formats accept one positive
physical `page`.

Direct mode returns exactly one image. A multi-page document therefore needs a single `page`;
without one, Typst cannot write the fixed output and the request fails with
`template_compile_failed`.

## Multi-page PNG and SVG

```json
{
  "format": "png",
  "archive": "zip",
  "pages": "1,3-6,8-",
  "ppi": 144,
  "filename": "report-pages.zip"
}
```

`archive: "zip"` always returns a ZIP, even if only one page matches. It makes `page` invalid and
allows the shared `pages` syntax; omitting `pages` selects all pages within the configured limit.
Conversely, `pages` is invalid without archive mode.

Entries are ordered by physical page number and named `page-01.png` or `page-01.svg`. Prelum asks
Typst for at most `PRELUM_MAX_OUTPUT_FILES + 1` candidate pages. The extra page is a sentinel used
to reject an oversized result rather than silently return a partial archive.

The uncompressed sum of the page files and the final ZIP must each fit
`PRELUM_MAX_OUTPUT_BYTES`. Archives use `application/zip`, `Content-Disposition: attachment` and
`Cache-Control: no-store`.
