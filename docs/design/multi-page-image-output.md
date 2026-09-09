# Multi-page PNG and SVG Output Design

**Date:** 2026-08-31  
**Status:** Implemented  
**Scope:** Prelum `POST /v1/render`

## Background

Prelum returns exactly one HTTP artefact per request. A PDF artefact can contain multiple pages.
Direct PNG and SVG use a fixed output path and therefore select one physical page, while explicit
archive mode packages several generated page files into one ZIP artefact.

Typst 0.15.1 already supports multi-page PNG and SVG export in one compiler invocation. It requires
the output filename to contain a page-number template such as `{p}` or `{0p}` and writes one file per
selected physical page. Typst ignores selected page numbers beyond the document and can exit
successfully without writing a file when no selected page exists.

The archive capability is not a second rendering mechanism. It is an explicit HTTP contract for
transporting several output files, bounded collection of Typst's output files, and safe packaging
into one response body.

## Goals

- Export selected or all physical pages from a Typst document as PNG or SVG in one request.
- Preserve the existing direct single-image response contract.
- Make the response media type knowable from the request without first compiling the document.
- Bound the number and aggregate size of generated files.
- Invoke Typst exactly once through the existing render path.
- Keep `source` non-empty and required for every request.
- Preserve the existing inline files, mounted fonts, mounted local packages, network controls,
  timeout, authentication, error handling, and observability rules.
- Deliver the change test-first and remove duplication only while the relevant tests are green.

## Non-goals

- Returning images as base64 inside JSON.
- A `multipart/mixed` response.
- Persisting individual pages or adding page-download endpoints, render jobs, or server-side
  storage.
- Supporting archive formats other than ZIP.
- Adding multi-file PDF output or changing PDF page-selection semantics.
- Adding a template registry or making `source` optional.
- Changing request-supplied or operator-mounted font and package behaviour.

## API contract

### Direct image output remains unchanged

The existing request continues to return one image:

```json
{
  "source": "#text(\"first\")\n#pagebreak()\n#text(\"second\")",
  "output": {
    "format": "png",
    "page": 2,
    "ppi": 144,
    "filename": "second-page.png"
  }
}
```

`page` is a positive physical page number. The response is `image/png` or `image/svg+xml` with
`Content-Disposition: inline`. Omitting `page` still works for a one-page document and still fails
with 422 if Typst cannot write the fixed output because the document has multiple pages.

### ZIP archive output

Multi-page export is explicitly selected with `archive: "zip"`:

```json
{
  "source": "#text(\"first\")\n#pagebreak()\n#text(\"second\")",
  "output": {
    "format": "png",
    "archive": "zip",
    "pages": "1,3-5,8-",
    "ppi": 144,
    "filename": "report-pages.zip"
  }
}
```

The PNG and SVG output fields are:

| Field | Direct image | ZIP archive | Contract |
|---|---:|---:|---|
| `format` | required | required | `png` or `svg` |
| `filename` | optional | optional | Suggested image name in direct mode; suggested archive name in ZIP mode |
| `page` | optional | forbidden | One positive physical page number |
| `archive` | omitted | required | The only accepted value is `zip` |
| `pages` | forbidden | optional | Comma-separated positive physical pages and ranges; omission selects all pages |
| `ppi` | PNG only | PNG only | Integer from 1 to 300; defaults to 144 |

`pages` uses the existing PDF selector grammar: `1`, `2-5`, `8-`, or a comma-separated combination.
Only ASCII digits are accepted. Closed ranges cannot descend, whitespace is not accepted, the
encoded value remains length-bounded, and the number of selector segments remains structurally
bounded. PDF and image output must use one shared parser and one statement of these rules.

The models remain strict. In particular:

- `page` and `pages` cannot be combined;
- `pages` requires `archive: "zip"`;
- `archive` is not accepted for PDF;
- `ppi` remains PNG-only;
- unknown archive values and format-inappropriate fields return the existing 400
  `invalid_request` response;
- `source` remains a required non-empty string in both direct and archive mode.

Archive mode is explicit even when the selected result contains one page. This keeps the response
type deterministic: `archive: "zip"` always returns a ZIP, and its absence never does.

### ZIP response

A successful archive response has:

```text
Content-Type: application/zip
Content-Disposition: attachment; filename="report-pages.zip"
Cache-Control: no-store
```

When `filename` is omitted, the archive name is `rendered.zip`. Existing filename sanitisation is
reused and corrects the extension to `.zip`.

ZIP entries are placed at the archive root and named from their physical page number:

```text
page-01.png
page-02.png
page-03.png
```

or equivalently with `.svg`. Padding is at least two digits and grows to fit the highest returned
physical page number. Entry order is ascending by physical page number. Names are generated by
Prelum, never by the caller or from filesystem-relative paths. ZIP timestamps and permission bits
are fixed so packaging does not introduce request-time metadata into otherwise identical output.

The archive contains only the generated page images. It does not include source, request files,
mounted resources, diagnostics, or a manifest.

## Limits and page selection

Add `PRELUM_MAX_OUTPUT_FILES`, an integer of at least one with a default of 64. It limits the number
of files in one archive independently of `PRELUM_MAX_OUTPUT_BYTES`.

The page-selection component owns parsing, normalisation, and bounding. The model uses it to validate
syntax; the renderer uses the same parsed representation with `max_output_files`. There must not be
a second range parser in the renderer.

Typst must never be asked to emit an unbounded number of images:

- a finite selection whose distinct page count exceeds the configured limit is rejected before
  starting Typst;
- an omitted selection is compiled as physical pages `1` through `limit + 1`;
- closed ranges and duplicate or overlapping segments are normalised by physical page number;
- multiple open ranges collapse into the one with the lowest start, and finite pages at or above
  that start are already covered by it;
- an open range is capped after the first `limit + 1` distinct candidate pages, after accounting
  for finite pages below its start;
- if Typst writes more than `limit` of those candidates, the render is rejected and no partial
  archive is returned.

Physical page numbers are contiguous, which makes the open-range bound lossless: if Typst can emit
any page from the open range, every selected lower physical page also exists. The extra candidate is
only a sentinel used to distinguish a complete result from one that would exceed the limit. It is
never returned. This algorithm must not silently truncate a successful archive.

Out-of-document page numbers are ignored by Typst. An explicit selection that matches no document
page therefore produces 422 `template_compile_failed`; it must not escape as a 5xx merely because
Typst exited successfully without creating a file. The same classification is applied to the
existing direct `page` option. A successful compiler exit without output when no explicit page was
selected remains `render_failed`, because it violates the expected compiler contract rather than a
caller page choice.

The uncompressed sum of all page files and the completed ZIP response must each be no greater than
`PRELUM_MAX_OUTPUT_BYTES`. The renderer checks file metadata and the aggregate before reading page
contents. ZIP creation runs off the event loop. Exceeding the byte limit keeps the existing 413
`output_too_large` response.

Exceeding the file-count limit returns 413 with a new stable error code:

```json
{
  "code": "too_many_output_files",
  "title": "Too Many Output Files",
  "status": 413,
  "detail": "...",
  "instance": ".../v1/render"
}
```

The error is caller-visible and is not classified as a server incident. No error or log includes
source content or request files.

## Internal design

### One output plan and one compile path

The validated PNG and SVG models share an image-output base model for `page`, `archive`, `pages`, and
their cross-field validation. PNG adds only `ppi`. PDF remains a separate output model.

One output-plan factory converts a validated output model into:

- the Typst output path or page-number path template;
- bounded `--pages` and format-specific CLI arguments;
- the final response extension, media type, and disposition;
- single-file or ZIP finalisation metadata.

This is the sole owner of the mapping between output request and output transport. The route does not
inspect `RenderOutput` to rediscover whether a response is an archive.

`TypstRenderer.render(RenderJob)` continues to create one temporary project and call `_run_typst`
exactly once. Archive mode supplies a Prelum-owned output template such as
`output-{p}.png`. The template and final input path remain the last two Typst arguments, preserving
the renderer-test indexing invariant.

After compilation, archive finalisation:

1. enumerates only regular, non-symlink files matching the exact Prelum-owned output pattern;
2. parses the physical page number and sorts numerically;
3. detects an empty explicit selection and the file-count sentinel;
4. checks aggregate uncompressed size without loading the files;
5. creates the ZIP in memory using Python's standard library with fixed entry metadata;
6. checks final response size;
7. saves only the completed ZIP when debug output is configured;
8. returns one `RenderResult` containing the archive bytes and transport metadata.

No new runtime dependency is required. Temporary page files remain inside the existing per-render
temporary directory and are removed with it.

### Response metadata

`RenderResult` gains an explicit disposition value (`inline` or `attachment`). Single PDF, PNG, and
SVG results set `inline`; ZIP results set `attachment`. The shared route executor uses that value
when building `Content-Disposition`, without branching on media type, filename extension, or request
model.

The output-size histogram continues to observe the actual HTTP response byte count and retains the
existing `output_format` label (`png` or `svg`). No archive label is added. Successful archive logs
include the bounded output file count and response byte count without filenames or request data.

### DRY ownership rules

- One page-selector parser owns syntax, range ordering, normalisation, and bounded expansion for PDF
  and image output.
- One image-output base model owns `page`/`archive`/`pages` compatibility; PNG and SVG do not repeat
  it.
- One output-plan factory owns Typst target, response extension, media type, and disposition.
- One size-checking helper enforces `max_output_bytes` for both a single output and a collection.
- One filename sanitiser handles both image and archive response names.
- One route executor builds successful responses and records metrics from `RenderResult`.
- `_run_typst` remains the only subprocess path and `_typst_output_args` remains the only owner of
  format-specific Typst flags.
- Existing request-file key validation, UTF-8 encoding, base64 decoding, Typst environment, local
  package path, font paths, and incident classification are reused without archive-specific copies.

## TDD delivery sequence

Every step follows red-green-refactor. A production behaviour is changed only after a focused test
demonstrates the missing behaviour. Refactoring or consolidation follows only after the focused test
is green.

### 1. Pin the request contract

First add failing model and route tests covering:

- `archive: "zip"` with omitted `pages` for PNG and SVG;
- finite, overlapping, and open page selections;
- PNG archive `ppi`;
- rejection of `page` with archive, `pages` without archive, archive on PDF, and unknown archives;
- strict rejection of format-inappropriate and misspelt fields;
- unchanged direct PNG and SVG models;
- required and non-empty `source` in archive requests;
- OpenAPI discrimination for PDF, PNG, and SVG output.

Then introduce the shared image-output model and the minimum validation required to pass those tests.

### 2. Extract and pin page-selection ownership

Before changing CLI construction, move the existing PDF page syntax into one reusable parser and
keep all current PDF tests green. Add example and Hypothesis property tests proving that:

- parse/normalise/serialise preserves the selected physical-page set for finite selectors;
- duplicate and overlapping ranges never inflate the distinct count;
- bounded expansion never asks Typst for more than `limit + 1` distinct pages;
- an open range preserves every requested candidate up to its sentinel boundary;
- the sentinel causes rejection instead of silent truncation whenever the document has more than
  the configured number of selected pages;
- invalid, descending, zero, negative, whitespace-containing, oversized, and over-segmented values
  are rejected by the one parser.

Only then use the parsed selection for image archives.

### 3. Pin output planning and Typst arguments

Add failing renderer unit tests showing that:

- direct output still uses `output.png` or `output.svg`;
- archive output uses the `{p}` filename template;
- omitted and open selectors become bounded `--pages` values;
- finite selections over the configured file limit fail before subprocess creation;
- PNG `--ppi`, mounted font paths, local package paths, empty package cache, and proxy controls are
  unchanged;
- Typst input and output remain the last two arguments.

Implement the output plan and adapt the existing `_run_typst` call rather than introducing another
compile method.

### 4. Pin collection, limits, and ZIP bytes

Add failing unit tests with generated temporary page files for:

- numeric ordering and fixed root-level entry names;
- fixed ZIP metadata;
- PNG and SVG entry extensions;
- an archive containing one selected page;
- rejection of the count sentinel without returning a partial archive;
- aggregate raw bytes and final ZIP bytes at and over the configured boundary;
- ignoring unrelated temporary files and rejecting a matching symlink or non-regular file as an
  infrastructure fault;
- an explicit selection matching no page as 422;
- no output without an explicit selection as a server fault;
- debug-copy behaviour for the completed archive only.

Implement ZIP construction with the standard library and reuse the extracted size and filename
helpers for direct output.

### 5. Pin the HTTP response

Add route tests asserting exact `Content-Type`, `Content-Disposition`, `Cache-Control`, response
filename correction, output-size metric value, and the stable `too_many_output_files` problem code.
Update `RenderResult` and make the shared route consume its disposition. Existing PDF and direct
image response tests must remain unchanged.

### 6. Prove the real compiler integration

Add non-skipping `integration` tests using the real pinned Typst CLI:

- a three-page PNG archive selecting pages 1 and 3 has exactly two valid PNG entries;
- PNG archive dimensions reflect the requested `ppi`;
- a multi-page SVG archive with omitted `pages` contains every page as valid SVG;
- an open range returns only matching physical pages;
- an explicit selection beyond the document returns 422 rather than 5xx;
- a document exceeding `max_output_files` returns 413 and no partial ZIP;
- the existing one-page direct response and selected single-page response still return image bytes,
  not ZIP bytes.

These tests must not skip when `typst` is missing; the existing test and Docker conventions apply.

### 7. Documentation and verification

Update README request fields, examples, response headers, errors, limits, and configuration table.
State explicitly that ZIP is the only multi-file response and that `source` remains required. Do
not imply that mounted fonts or packages select a template.

Run:

```bash
uv run pytest -m "not integration"
uv run pytest -m integration
uv run ruff check .
uv run ty check app
make test-docker
make build
```

## Acceptance criteria

- Existing direct PDF, PNG, and SVG requests retain their response types and headers.
- PNG and SVG accept explicit ZIP archive mode and return `application/zip` with attachment
  disposition.
- Archive response type is determined solely by the validated request, not document page count.
- ZIP entries correspond to selected physical pages, are numerically ordered, safely named, and
  contain valid images of the requested format.
- PNG `ppi` applies to every archived page.
- Typst is invoked once per render and is never given an unbounded image-page selection.
- File count, aggregate raw size, and final response size are bounded without returning partial
  archives.
- An archive is never reported as successful with a silently truncated page selection.
- A page selection matching no page returns 422, including the existing direct `page` case.
- The route derives response headers from `RenderResult` without duplicating output-model rules.
- Page-selection syntax and bounded expansion have one implementation shared by PDF and image
  output.
- `source` remains required; inline files and read-only font and local-package mounts retain their
  current behaviour.
- Unit, property, real-Typst integration, Docker, lint, type, and runtime-image checks pass.
