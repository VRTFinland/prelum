# Prelum Render API v1 Design

**Date:** 2026-08-29
**Updated:** 2026-09-01
**Status:** Implemented
**Scope:** Prelum `POST /v1/render`

## Context

Prelum is an independent, inline-only rendering service. It accepts a complete caller-supplied
Typst project, binds JSON data into its source, and returns one rendered artefact. It does not own a
template registry or business-specific template and organisation concepts.

The v1 contract deliberately exposes only inputs that affect compilation or returned artefact
metadata. All request models are strict, resources are explicit, and one domain request reaches one
renderer path.

## Goals

- Publish a small, versioned render contract for PDF, PNG and SVG.
- Preserve arbitrary JSON values when binding them to Typst.
- Make every request-specific source, image, font and other resource an explicit project file.
- Allow operators to mount shared fonts and versioned local packages read-only.
- Keep one request model, one render job and one compile path.
- Bound request, queue, compiler, file and output resources.
- Keep network access closed unless an operator enables it deliberately.
- Classify caller input as 4xx and infrastructure failures as 5xx.
- Develop behaviour test-first and centralise each validation policy.

## Non-goals

- A server-side template registry or template lookup by name.
- Multipart requests; JSON with explicit base64 file content is sufficient for the configured
  limits.
- Remote URLs for templates, images, fonts or packages.
- Page-layout semantics. Templates own page size, margins, headers and footers.
- Per-template or per-organisation business metrics.
- Optional source or a second rendering path.

## Endpoint and authentication

```text
POST /v1/render
Content-Type: application/json
X-Prelum-Api-Token: <shared token>
```

The API token is required on the render endpoint. Settings fail closed outside the explicitly named
development environments, and token comparison is constant-time. OpenAPI represents the same
dependency as the required `PrelumApiToken` API-key scheme.

`GET /health`, `GET /metrics`, `/openapi.json`, `/docs` and `/redoc` are unauthenticated operational
and documentation endpoints.

## Request contract

```json
{
  "source": "#import \"lib/label.typ\": caption\n#image(data.logo)\n#caption(data.name)",
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

| Field | Required | Contract |
| --- | ---: | --- |
| `source` | yes | Non-empty Typst source compiled as `main.typ` |
| `files` | no | Map of project-relative POSIX paths to explicit file objects; defaults to `{}` |
| `data` | no | Any JSON value, converted directly to Typst; defaults to `{}` |
| `output` | no | Strict format-specific output options; defaults to PDF |

Every file object contains exactly `encoding` (`text` or `base64`) and `content`. Unknown fields,
string file shorthand and format-inappropriate output options are rejected.

The output union contains:

- PDF `version`, `standards` and physical `pages` selection;
- PNG `ppi`, one direct `page`, or explicit multi-page ZIP `archive` and `pages`;
- SVG one direct `page`, or explicit multi-page ZIP `archive` and `pages`;
- an optional sanitised `filename` for every format.

The detailed public contract is maintained in [Render request](../api/render.md) and
[Output formats](../api/output-formats.md). Generated OpenAPI owns the machine-readable model.

## Data binding

Prelum prepends two aliases for the exact converted JSON value:

```typst
#let request = <data converted to Typst>
#let data = request
```

Omitted `data` is an empty Typst dictionary and explicit JSON `null` is Typst `none`. Objects,
lists, scalars and strings otherwise retain their JSON structure and meaning. Asset paths are
ordinary strings referring to explicit `files` entries.

All caller text reaches UTF-8 through one encoder. `_TYPST_ESCAPES` is the only definition of
characters unsafe inside a generated Typst string literal.

## Files and resources

`app/render/templates.py` is the sole owner of acceptable `files` keys. It validates normalised
POSIX-relative paths, the portable segment bound, the reserved `main.typ` name, case collisions,
file/directory collisions, the structural key-count bound and the `.wasm` suffix speed bump.

The renderer owns content decoding and configurable byte/count limits. It writes accepted files
inside one temporary project and passes that project as Typst's `--root` and first `--font-path`.

`PRELUM_FONT_PATH` may add one read-only font directory. `PRELUM_LOCAL_PACKAGE_PATH` may replace
the empty package path with one read-only local package root. The package cache remains empty per
render, and these mounts add no template lookup by name.

## Output and errors

A direct result returns PDF, PNG or SVG bytes with `Content-Disposition: inline` and
`Cache-Control: no-store`. Explicit image archive mode returns one deterministic ZIP with
`Content-Disposition: attachment`. Both direct and archive bytes are bounded.

Errors use `application/problem+json` with stable `code`, `title`, `status`, `detail` and `instance`
members. Callers branch on `code`. Validation responses omit mirrored input, caller-controlled
message values are bounded, and Typst diagnostics are discarded because they can quote source.

`status_is_server_fault` is the one incident boundary: caller input is 4xx, including a 422 compile
failure, while infrastructure and unexpected failures are 5xx. See [Errors](../api/errors.md) for
the published codes.

## Internal design

The API boundary mechanically creates one immutable job:

```text
RenderRequest ──► RenderJob ──► TypstRenderer.render ──► RenderResult
```

`RenderJob` carries `source`, `files`, `data` and the validated output union. The renderer creates
one temporary layout, writes and binds the project, calls Typst once, validates the expected output,
and returns its bytes, content type, filename and disposition.

DRY ownership rules are load-bearing:

- file-key policy belongs only to `app/render/templates.py`;
- page selection syntax belongs only to `parse_page_selection`;
- one decoder owns file encodings and decoded-size enforcement;
- one format map owns extensions and content types;
- one escape table owns Typst string safety;
- one status helper owns caller/server incident classification;
- request models validate transport shape but perform no rendering or filesystem work.

## Isolation and limits

- Body middleware counts bytes received rather than trusting `Content-Length`.
- Source, strings, files, file count, direct output, archive aggregate and archive file count are
  bounded independently.
- A semaphore caps Typst processes and bounded queue waiting sheds excess load with 429.
- Each compiler process has a timeout and is killed on timeout or request cancellation.
- Closed proxy variables and empty package directories block normal package downloads by default.
- Layout-related caller errors become 4xx while storage and permission failures remain server
  faults.

The `.wasm` suffix check is not a plugin boundary because Typst identifies plugins from their magic
bytes. Deployment-level isolation remains necessary when compiling caller programs.

## Observability

Renderer metrics use fixed-cardinality labels such as output format and status. They cover total
renders, durations, output sizes, queue depth, concurrency, timeouts and error types. Caller-defined
template names, organisation identifiers, filenames and paths never become labels.

Compile failure logs contain the exit code and owned temporary template path, not compiler output or
request content. Unexpected failures are logged as errors and reported to Sentry when configured.

## Verification

The contract is pinned at several levels:

- strict model and API tests for every format, option combination and problem classification;
- property tests for file-key and page-selection invariants;
- renderer unit tests for command construction, limits and filesystem backstops;
- integration tests compiling representative projects with the real pinned Typst binary;
- Docker tests using the production compiler and font environment;
- generated OpenAPI tests for routes, required source, authentication and response media types.

## Acceptance criteria

- `/v1/render` is the sole render endpoint and requires `source` and API-key authentication.
- Every request-specific resource uses an explicit file object.
- JSON data reaches Typst without hidden structural transformation.
- Mounted fonts and local packages work without a registry or optional source.
- Every request reaches one `TypstRenderer.render(RenderJob)` path and one compiler invocation.
- Caller-controlled validation cannot escape as a 5xx.
- Public docs, generated OpenAPI, unit tests, integration tests and Docker tests describe the same
  contract.
