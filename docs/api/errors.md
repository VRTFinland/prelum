# Errors

Errors use `application/problem+json` with `code`, `title`, `status`, `detail` and `instance`:

```json
{
  "code": "render_queue_full",
  "title": "Too Many Requests",
  "status": 429,
  "detail": "Render queue is full; retry shortly",
  "instance": "https://prelum.example/v1/render"
}
```

Branch on `code`, not on `status` or `title`. Several different conditions use 413, while titles
are human-readable prose and may be reworded. Request bodies are never echoed. Typst diagnostics
are neither returned nor logged because they can quote caller-supplied source.

| Status | `code` | Cause |
| ---: | --- | --- |
| 400 | `invalid_request` | Missing, malformed, unknown or format-inappropriate request field |
| 400 | `unsupported_format` | `format` outside `pdf`, `svg` and `png` |
| 400 | `invalid_template_path` | Unsafe, non-normalised or reserved `files` key |
| 400 | `invalid_file_data` | Malformed base64, excessive or colliding entries, or non-UTF-8 text |
| 403 | `forbidden` | Missing or incorrect API token |
| 408 | `render_timeout` | Typst exceeded the render timeout |
| 413 | `request_too_large` | JSON body exceeded the request-body limit |
| 413 | `template_too_large` | Source or one inline file exceeded its limit |
| 413 | `string_too_large` | One string in `data` exceeded its limit |
| 413 | `output_too_large` | Direct output, aggregate images or ZIP exceeded the output limit |
| 413 | `too_many_output_files` | An image archive exceeded its file-count limit |
| 422 | `template_compile_failed` | Caller-supplied Typst or page selection could not produce the requested output |
| 429 | `render_queue_full` | No render slot became free before the queue deadline |
| 500 | `render_failed` | Typst reported success but produced no expected output |
| 503 | `service_unavailable` | Unexpected service or infrastructure failure |

A 429 includes `Retry-After`; callers should wait at least that many seconds before retrying.
