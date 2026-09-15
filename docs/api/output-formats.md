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

## Validate the options before you send them

Everything above is also published as data, so a client can refuse a bad `output` object without a
round trip — and prove its check agrees with the service instead of transcribing this page.

Two places carry the same document: `output_rules` in the response of
[`GET /v1/constraints`](files-key-rules.md), and the static
[`output-rules.json`](output-rules.json) for tools that run without a token. Nothing in it depends
on the deployment, so the two never disagree.

`output_rules_version` rises whenever a published output rule changes. It is separate from the
`rules_version` covering the files-key rules: the two contracts change at different rates, and one
counter would send you back through the key rules because a PDF standard was added.

| Field | What it carries |
| --- | --- |
| `formats` | The values `format` accepts |
| `pdf.formats`, `image.formats` | Which `format` values each block of rules governs. Between them they cover `formats`, so a client never has to read "not `pdf`, therefore an image" — a guess that would misapply the image rules to any format added later |
| `pdf.versions`, `pdf.standards` | The values `version` and `standards` accept |
| `pdf.max_standards` | How many entries `standards` may hold |
| `pdf.pdf_a_version` | Which PDF version each PDF/A standard requires. A standard absent from this map is not a PDF/A profile — which is how `ua-1` is the one that may accompany one |
| `pdf.tagged_standards` | The standards that require tagging, and so cannot be combined with `pages` |
| `pdf.pdf_a_4_standards` | The PDF/A-4 family, which `ua-1` is incompatible with |
| `image.archives`, `image.min_page` | What `archive` accepts, and the lowest `page` |
| `image.png` | `min_ppi`, `max_ppi` and the `default_ppi` used when `ppi` is omitted |
| `page_selection` | The `pages` grammar: `max_length` with the `max_length_unit` it counts, `max_selections`, and `selection_pattern`, which each comma-separated selection must match in full |
| `rules` | The rules that need more than one field to decide, each with a stable `id` |
| `conformance_vectors` | Executable examples — see below |

## Which rule rejected the request

Every rule in `rules` answers [`invalid_request`](errors.md) at the same place in the body, so the
response carries the rule's `id` in `context.rule`. Branch on that rather than on `detail`, which is
prose and may be reworded.

`rule` always names the failure `detail` reports. A request that breaks one of these rules and also
something they do not name — a missing `source`, say — reports that instead and carries no `rule`
until it is fixed.

| `id` | The request is refused when |
| --- | --- |
| `page_selection_too_long` | `pages` is longer than `max_length` |
| `page_selection_too_many_segments` | `pages` holds more than `max_selections` comma-separated selections |
| `page_selection_malformed` | A selection does not match `selection_pattern` in full |
| `page_range_end_precedes_start` | A closed range in `pages` ends before it starts |
| `duplicate_standards` | `standards` names the same standard twice |
| `multiple_pdf_a_standards` | More than one PDF/A standard is selected |
| `ua_1_with_pdf_a_4` | `ua-1` is combined with `a-4`, `a-4f` or `a-4e` |
| `version_conflicts_with_standard` | An explicit `version` is not the one the selected PDF/A standard requires. Omitting `version` is always accepted |
| `ua_1_with_pdf_2_0` | `ua-1` is combined with an explicit `version` of `2.0` |
| `pages_with_tagged_standard` | `pages` is combined with a standard that requires tagging |
| `pages_requires_archive` | An image output uses `pages` without `archive` |
| `page_with_archive` | An image output combines `page` with `archive` |

The rules are listed in the order Prelum applies them, and `rule_evaluation` says so in the document
itself. An object that breaks two of them is reported against the earlier one, so a client checking
them in another order disagrees about which constraint you broke while agreeing that you broke one.

The field-level rules are not in this list. An unknown field, a `ppi` outside its range or a
`format` that does not exist is described by the [OpenAPI schema](openapi.md), and the validation
error's own `type` already tells those apart.

## Conformance vectors

`conformance_vectors` holds accepted and rejected `output` objects with the answer Prelum gives:

```json
{
  "output": { "format": "pdf", "version": "1.4", "standards": ["a-2b"] },
  "accepted": false,
  "code": "invalid_request",
  "rule": "version_conflicts_with_standard"
}
```

An accepted vector has no `code` or `rule`. A rejected one always carries `code`, and carries `rule`
when one of the rules above refused it.

Run them against your own validator in your own test suite. Prelum runs every one of them against
the service on each build, and against a validator built from this document alone — so a vector that
disagreed with the service would fail here rather than in your deployment. That is the whole point
of publishing them: your check is tested against ours rather than copied from it.
