# Render request

`POST /v1/render` requires `Content-Type: application/json` and the
`X-Prelum-Api-Token` header. It is Prelum's only render endpoint.

## Complete request

```json
{
  "source": "#import \"lib/label.typ\": caption\n#image(data.logo, width: 16pt)\n#caption(data.name)",
  "files": {
    "lib/label.typ": {
      "encoding": "text",
      "content": "#let caption(name) = [Hello, #name!]"
    },
    "assets/logo.png": {
      "encoding": "base64",
      "content": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB..."
    }
  },
  "data": {
    "name": "World",
    "logo": "assets/logo.png"
  },
  "output": {
    "format": "pdf",
    "filename": "hello.pdf"
  }
}
```

## Top-level fields

| Field | Required | Behaviour |
| --- | ---: | --- |
| `source` | yes | Non-empty Typst source compiled as `main.typ` |
| `files` | no | Defaults to `{}`; maps project-relative paths to file objects |
| `data` | no | Defaults to `{}`; accepts any JSON value without structural transformation |
| `output` | no | Defaults to PDF; contains `format`, `filename` and format-specific options |

All request models are strict. Unknown fields, misspellings and options belonging to another output
format are rejected instead of being ignored.

## Files

Every file object requires both fields:

| Field | Values | Purpose |
| --- | --- | --- |
| `encoding` | `text` or `base64` | Select UTF-8 text or strictly decoded binary content |
| `content` | string | File content in the selected encoding |

String shorthand is not accepted. Files are written beside `main.typ`, so source imports or loads
them using the same relative path as the key.

Keys must be normalised POSIX relative paths using only `[A-Za-z0-9._-]` and `/`. Prelum rejects:

- absolute paths, traversal and non-normalised paths;
- a path segment longer than 255 characters;
- a key used as another key's directory;
- leading `main.typ` in any letter case;
- keys differing only in case;
- filenames ending in `.wasm`.

The `.wasm` rule is only a speed bump. Typst recognises plugins by their magic bytes rather than
their filename, so this check is not a security boundary.

## Data binding

Omitted `data` becomes an empty Typst dictionary and explicit JSON `null` becomes Typst `none`.
Prelum converts every other JSON value directly and prepends two names for it:

```typst
#let request = <data converted to Typst>
#let data = request
```

Put request-specific assets in `files` and refer to their keys with ordinary strings in `data`.

## Output

`output.format` accepts `pdf`, `png` or `svg` and defaults to `pdf`. `output.filename` is optional;
Prelum removes unsafe characters, bounds its length and corrects the extension before returning it.
See [Output formats](output-formats.md) for format-specific fields.
