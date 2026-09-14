# See what Prelum can produce

These four templates use the same `POST /v1/render` endpoint to produce an invoice, an asset label,
a multi-page report and this documentation manual. Select a preview to open its PDF, or use the
buttons below it to compare the PDF, PNG and SVG output.

Nothing is pre-installed in Prelum. Each render request carries its own Typst source, data and any
additional files, which is why one endpoint can produce documents with very different layouts.

The source files are available in the repository's
[`examples/`](https://github.com/VRTFinland/prelum/tree/main/examples) directory. Expand the
sections under a preview when you want to inspect the Typst template or its example data.

## Invoice

An A4 invoice: sender and customer blocks, a line-item table and computed totals. Quantities,
prices and the VAT rate come from the request, and the template derives every sum.

[![Invoice rendered to PNG](examples/invoice.png){ .prelum-example-preview width="320" }](examples/invoice.pdf)

[PDF](examples/invoice.pdf){ .md-button } [PNG](examples/invoice.png){ .md-button }
[SVG](examples/invoice.svg){ .md-button }

??? note "Template — examples/invoice/main.typ"

    ```typst
    --8<-- "examples/invoice/main.typ"
    ```

??? note "Data — examples/invoice/data.json"

    ```json
    --8<-- "examples/invoice/data.json"
    ```

## Label

An 80 × 50 mm asset label. Page size is a property of the template, not of the request: this and
the invoice above reach the same endpoint with the same options.

[![Label rendered to PNG](examples/label.png){ .prelum-example-preview width="320" }](examples/label.pdf)

[PDF](examples/label.pdf){ .md-button } [PNG](examples/label.png){ .md-button }
[SVG](examples/label.svg){ .md-button }

??? note "Template — examples/label/main.typ"

    ```typst
    --8<-- "examples/label/main.typ"
    ```

??? note "Data — examples/label/data.json"

    ```json
    --8<-- "examples/label/data.json"
    ```

## Multi-page report

A three-page report, one page per section. Direct PNG and SVG output returns a single image, so the
previews below name `page: 1`; the archive returns every page in one ZIP.

[![Report page one rendered to PNG](examples/report.png){ .prelum-example-preview width="320" }](examples/report.pdf)

[PDF](examples/report.pdf){ .md-button } [PNG, page 1](examples/report.png){ .md-button }
[SVG, page 1](examples/report.svg){ .md-button }
[ZIP, all pages](examples/report-pages.zip){ .md-button }

```json
{
  "format": "png",
  "archive": "zip",
  "ppi": 110
}
```

The archive contains `page-01.png`, `page-02.png` and `page-03.png`. Adding `pages` selects a
subset; see [Output formats](api/output-formats.md) for the selector grammar.

??? note "Template — examples/report/main.typ"

    ```typst
    --8<-- "examples/report/main.typ"
    ```

??? note "Data — examples/report/data.json"

    ```json
    --8<-- "examples/report/data.json"
    ```

## This documentation as a PDF

The manual below is this site. Every page in the navigation is converted to the block structure the
template typesets, so the PDF cannot describe a version of Prelum the site does not, and it is
rendered by the same build that publishes these pages.

It is also the example that carries an attachment: the logo travels in `files` under the key the
template imports it by, exactly as a caller would send a font, a signature image or a letterhead.

[![Manual cover rendered to PNG](examples/manual.png){ .prelum-example-preview width="320" }](examples/manual.pdf)

[PDF, complete manual](examples/manual.pdf){ .md-button .md-button--primary }
[PNG, cover](examples/manual.png){ .md-button }
[SVG, cover](examples/manual.svg){ .md-button }

The template holds no documentation text of its own. It is a cover, a running header and footer, a
table of contents, and one rule per block kind — which makes it the shortest complete illustration
of multi-page layout in this set.

```json
{
  "source": "<the template below>",
  "files": {
    "assets/prelum-logo-concept.svg": { "encoding": "text", "content": "<svg ...>" }
  },
  "data": {
    "title": "Prelum",
    "logo": "assets/prelum-logo-concept.svg",
    "chapters": []
  },
  "output": { "format": "pdf", "filename": "manual.pdf" }
}
```

??? note "Template — examples/manual/main.typ"

    ```typst
    --8<-- "examples/manual/main.typ"
    ```

## Render an example yourself

With Prelum running locally, execute this from the repository root to render the invoice. The `jq`
command reads the template and data files and assembles the JSON request:

```bash
curl --silent --show-error --fail-with-body \
  http://localhost:9870/v1/render \
  -H "Content-Type: application/json" \
  -H "X-Prelum-Api-Token: dev-only-insecure-token" \
  -d "$(jq -n \
        --rawfile source examples/invoice/main.typ \
        --slurpfile data examples/invoice/data.json \
        '{source: $source, data: $data[0], output: {format: "pdf"}}')" \
  --output invoice.pdf
```

See [Render request](api/render.md) for auxiliary `files`, and
[Output formats](api/output-formats.md) for the full set of output options.

The rendered files shown on this page are regenerated from the checked-in sources with
`make docs-examples`.
