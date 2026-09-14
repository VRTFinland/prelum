# Handle errors

When Prelum cannot render a request, it returns JSON describing what went wrong. A client normally
needs to do only two things: use the HTTP status and stable `code` to decide what to do, then show
the human-readable `detail` to the user.

Typical responses fall into these groups:

| Status | What it usually means | What the client should do |
| ---: | --- | --- |
| 400 or 422 | The request, file data or Typst source is invalid | Correct the request before trying again |
| 403 | The API token is missing or incorrect | Fix the client configuration |
| 408 | Typst exceeded the render timeout | Simplify the document or ask the operator about the limit |
| 413 | An input or output limit was exceeded | Read `code` and `context` to find the exact limit |
| 429 | All render slots remained busy | Wait for the `Retry-After` duration, then retry |
| 500 or 503 | Prelum or its infrastructure failed | Retry cautiously and alert the service operator |

## Example response

Every error uses `application/problem+json` and has the same top-level fields:

```json
{
  "code": "template_file_too_large",
  "title": "Template Too Large",
  "status": 413,
  "detail": "Template file 'assets/logo.png' size 1500000 bytes exceeds limit 1048576",
  "instance": "https://prelum.example/v1/render",
  "context": {
    "key": "assets/logo.png",
    "size": 1500000,
    "limit": 1048576
  }
}
```

In application logic, compare `code` rather than `title` or `detail`: those two fields are prose and
may be reworded. `context` contains details such as the measured size, configured limit or affected
file key. It is `{}` when the error needs no additional values.

Several different conditions use status 413, so the status alone cannot tell a client what was too
large. The `code` distinguishes the request body, source, one file, one data string and the rendered
output.

## Context fields

A field means the same thing wherever it appears.

Caller text in prose, validation errors and `path` escapes lone surrogates as literal `\uXXXX`
text before shortening, so these fields can always be encoded as UTF-8.

| Field | Type | Meaning |
| --- | --- | --- |
| `limit` | integer | The configured limit that was exceeded, in the unit the code counts |
| `size` | integer | A measured size in bytes |
| `count` | integer | A measured or requested number of things |
| `key` | string | One `files` key, complete and exactly as sent |
| `rule` | string | The `id` of the whole-set files-key rule that rejected the request, as published by `GET /v1/constraints` |
| `path` | array | Where a value sits in `data`: object keys as strings, array indices as integers. String segments longer than 80 characters are shortened with `…` |
| `subject` | string | `key` or `value`: which string at `path` is at fault. An oversized object key is its own last path segment, so the path alone cannot say |
| `declared_size` | integer | The `Content-Length` the caller sent; not a measurement |
| `timeout_secs` | integer | The configured render timeout in seconds |
| `retry_after` | integer | Seconds to wait before retrying, equal to the `Retry-After` header |
| `errors` | array | Request validation errors as `{loc, msg, type}`, at most 20 of them. `loc` is shortened like `path`: string segments over 80 characters end in `…`, array indices stay integers. `msg` is prose that may quote the offending value, and is shortened the same way past 200 characters |
| `errors_total` | integer | How many validation errors there were, present only when `errors` holds fewer than that |

## Codes

| Status | `code` | `context` | Cause |
| ---: | --- | --- | --- |
| 400 | `invalid_request` | `errors`, absent when the body could not be parsed at all | Missing, malformed, unknown or format-inappropriate request field, or a body no JSON parser accepts |
| 400 | `unsupported_format` | `errors` | `format` outside `pdf`, `svg` and `png` |
| 400 | `invalid_template_path` | `errors` | Unsafe, non-normalised or otherwise invalid `files` key |
| 400 | `invalid_file_data` | `key` or `count` + `limit`, `rule` when a whole-set rule rejected it, and `errors` only when validation raised it (see below) | Malformed base64, excessive or colliding entries, or non-UTF-8 text |
| 403 | `forbidden` | — | Missing or incorrect API token |
| 404 | `not_found` | — | No route at the requested path |
| 405 | `method_not_allowed` | — | The route exists but not for this method; `Allow` names the ones it has |
| 408 | `render_timeout` | `timeout_secs` | Typst exceeded the render timeout |
| 413 | `request_too_large` | `limit`, `declared_size` when `Content-Length` was sent | JSON body exceeded the request-body limit |
| 413 | `template_source_too_large` | `size`, `limit` | `source` exceeded its limit |
| 413 | `template_file_too_large` | `key`, `size`, `limit` | One `files` entry exceeded its limit after decoding |
| 413 | `string_too_large` | `path`, `subject`, `size`, `limit` | One string in `data` exceeded its limit |
| 413 | `output_too_large` | `size`, `limit` | Direct output, aggregate images or ZIP exceeded the output limit |
| 413 | `page_selection_too_large` | `count`, `limit` | `pages` selects more pages than an archive may hold; nothing was rendered |
| 413 | `too_many_output_files` | `limit` | The document produced more pages than an archive may hold |
| 422 | `template_compile_failed` | — | Caller-supplied Typst or page selection could not produce the requested output, including a render killed for exhausting memory |
| 429 | `render_queue_full` | `retry_after` | No render slot became free before the queue deadline |
| 500 | `render_failed` | — | Typst was killed by a signal the render's own memory limit does not explain, or reported success but produced no expected output |
| 503 | `service_unavailable` | — | Unexpected service or infrastructure failure |

`invalid_file_data` carries `key` when one `files` entry is at fault — malformed base64, text that
is not UTF-8, a key that escapes the project root or collides with another in case or as a
directory, or a layout that cannot be written — and `count` with `limit` when there are too many
entries. Non-UTF-8 `source` carries neither. `errors` accompanies these fields only for the causes
request validation detects (the key collisions and the structural key-count cap); the causes the
renderer detects have no validation error list to publish. `invalid_template_path` never carries the key: it has not passed validation, so it is not
echoed; `detail` names it, shortened.

A 429 includes `Retry-After`; callers should wait at least that many seconds before retrying.

## Privacy of error responses

Request bodies are never echoed. Typst diagnostics are neither returned nor logged because they
can quote caller-supplied source. Caller-controlled paths and validation messages are shortened and
made safe for UTF-8 before appearing in a response; see the context descriptions above for the few
bounded values that are returned exactly.
