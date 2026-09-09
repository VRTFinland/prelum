# Security model

Prelum compiles source supplied by an authenticated caller. Authentication controls who may ask
for a render; it does not make that source trusted.

## Controls

- The service requires a shared API token outside explicitly named development environments and
  compares it in constant time.
- The container and Typst subprocess run as uid 10001 rather than root.
- Every render has its own temporary project, which is passed to Typst as `--root`.
- Request file keys cannot escape or ambiguously address that project.
- Render concurrency, queue time, compiler time, request sizes and output sizes are bounded.
- Proxy variables point to a closed local port by default, and package cache/path arguments point
  to an empty per-render directory unless a local package mount is configured.
- Typst diagnostics are discarded because they may quote caller source.
- Mounted font and package directories are treated as read-only resources.
- The Typst subprocess receives an allowlisted environment rather than the service's own, so
  neither the API token and Sentry DSN nor `TYPST_*` argument overrides are visible to it.

## Network access

No released Typst version has an offline flag. Prelum blocks normal compiler package downloads by
combining a closed proxy environment with empty package directories. Set
`PRELUM_ALLOW_TYPST_NETWORK=true` only when remote package access is intentional, and use
deployment-level egress policy as defence in depth.

## Plugins

Prelum rejects request filenames ending in `.wasm`, but Typst identifies plugins from magic bytes
rather than filenames. This reduces accidental plugin execution and is explicitly not a security
boundary. Deploy Prelum only where compiling authenticated caller source is acceptable.

## Error privacy

Validation responses omit Pydantic's mirrored `input`. Caller-controlled values included in error
messages are bounded, and compiler stdout/stderr is neither returned nor logged. Logs and Sentry
must not receive request bodies, credentials, proprietary templates or generated artefacts. Sentry
is configured accordingly: request bodies are never attached, and the API token header is redacted
before an event leaves the process, because the SDK's own redaction list covers only the header
names it ships with.
