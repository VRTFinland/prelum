# Render a document

Prelum turns a caller-supplied [Typst](https://typst.app/) document into a PDF, PNG or SVG. Send
the document as JSON to `POST /v1/render`; a successful response contains the finished file itself.

## Try the smallest request

This request needs only a `source`. The output defaults to PDF:

```bash
curl --silent --show-error --fail-with-body \
  http://localhost:9870/v1/render \
  -H "Content-Type: application/json" \
  -H "X-Prelum-Api-Token: dev-only-insecure-token" \
  --data '{"source":"= Hello\nRendered by Prelum."}' \
  --output hello.pdf
```

Open `hello.pdf` after the command completes. When using your own deployment, replace the URL and
development token with its configured values.

## Build a complete request

A document can also receive data and use additional project files:

```json
{
  "source": "#include \"greeting.typ\"\n\n#data.name",
  "files": {
    "greeting.typ": {
      "encoding": "text",
      "content": "Hello from Prelum!"
    }
  },
  "data": {
    "name": "Ada"
  },
  "output": {
    "format": "pdf",
    "filename": "hello.pdf"
  }
}
```

Here is what happens when Prelum receives this request:

- `source` is the Typst document that Prelum compiles;
- `files` adds `greeting.typ` beside that document, so `#include "greeting.typ"` can read it;
- `data` becomes the Typst value named `data`, making `#data.name` produce `Ada`;
- `output` asks for a PDF returned with the filename `hello.pdf`.

Only `source` is required. Omit `files` and `data` when the document does not need them, and omit
`output` when the default PDF is suitable.

## Add project files

Use `files` for Typst modules, images, fonts or other assets that belong to this one render. Each
entry has a project-relative path and an object containing:

| Field | Value | Meaning |
| --- | --- | --- |
| `encoding` | `text` | `content` is UTF-8 text, suitable for `.typ`, `.json` or `.csv` files |
| `encoding` | `base64` | `content` is Base64-encoded binary data, suitable for images or fonts |
| `content` | string | The text or Base64 data to write into the project |

For example, a PNG can be supplied as follows and then read in Typst with
`#image("assets/logo.png")`:

```json
{
  "files": {
    "assets/logo.png": {
      "encoding": "base64",
      "content": "<Base64-encoded PNG data>"
    }
  }
}
```

The string placeholder must be replaced with actual Base64 data. Prelum decodes it before checking
the per-file size limit.

File paths use `/` even when the client runs on Windows. Straightforward paths such as
`lib/report.typ` and `assets/logo.png` work as written. Paths that escape the project, contain empty
segments or conflict with another entry are rejected. See
[Constraints and files-key rules](files-key-rules.md) for the complete rules and for
`GET /v1/constraints`, which reports the active deployment limits.

`main.typ` is an ordinary file key; Prelum stores `source` under an internal name that valid keys
cannot collide with.

## Pass data to Typst

`data` accepts any JSON value: an object, array, string, number, boolean or `null`. Prelum converts
it to a Typst value without changing its structure. Access an object field such as
`{"customer":{"name":"Ada"}}` with `data.customer.name` in the Typst source.

When `data` is omitted, it is an empty Typst dictionary. JSON `null` becomes Typst `none`.
Internally, Prelum makes the converted value available under two equivalent names:

```typst
#let request = <data converted to Typst>
#let data = request
```

Use `files` for the actual bytes of an asset and `data` for its path. For example,
`"logo": "assets/logo.png"` in `data` can be read with `#image(data.logo)`.

## Choose the output

`output.format` accepts `pdf`, `png` or `svg`. PDF is the default. The optional `filename` controls
the suggested download name; Prelum removes unsafe characters, limits its length and corrects the
extension when necessary.

PNG and SVG can return one page directly or multiple pages in a ZIP archive. PDF supports page
selection, version selection and conformance standards. See [Output formats](output-formats.md) for
the available options and examples.

## Handle the response

A successful request returns the rendered bytes, not JSON. Its `Content-Type` identifies the file,
and `Content-Disposition` supplies the safe filename. Direct outputs use
`Content-Disposition: inline`; ZIP archives use `attachment`. Responses also include
`Cache-Control: no-store`.

If the request is invalid, Prelum returns `application/problem+json`. Use its stable `code` and
`context` fields in application logic, and show its human-readable `detail` to the user. See
[Errors](errors.md) for the response shape and all error codes.

## Request reference

| Field | Required | Behaviour |
| --- | ---: | --- |
| `source` | yes | Non-empty Typst source, compiled as the project entry point |
| `files` | no | Defaults to `{}`; maps project-relative paths to file objects |
| `data` | no | Defaults to `{}`; accepts any JSON value |
| `output` | no | Defaults to PDF; contains the format and its options |

Request objects are strict. Unknown fields, misspellings and options belonging to another output
format are rejected rather than silently ignored. The render endpoint validates every request;
client-side checks using `/v1/constraints` are an optional way to provide earlier feedback. Both
halves of that check are published: the key rules in
[Constraints and files-key rules](files-key-rules.md), and the `output` rules with their conformance
vectors in [Choose an output format](output-formats.md).
