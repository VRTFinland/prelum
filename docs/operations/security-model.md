# Security model

Prelum runs Typst source supplied by its callers. This is suitable when authenticated users are
allowed to compile documents inside a service you operate. Authentication controls who may submit
a render; it does not make their source trusted.

Before exposing Prelum outside a development machine:

- use a private, rotated API token and restrict network access to intended clients;
- keep Typst package downloads disabled unless they are explicitly required;
- mount fonts and local packages read-only;
- choose request, concurrency, timeout and output limits for the available capacity;
- keep credentials, request bodies, templates and rendered documents out of logs and telemetry;
- add deployment-level process, filesystem and egress controls appropriate to your environment.

The sections below explain what Prelum itself enforces and where an operator still owns the
boundary.

## Controls

- The service requires a shared API token outside explicitly named development environments and
  compares it in constant time.
- The container and Typst subprocess run as uid 10001 rather than root.
- Every render has its own temporary project, which is passed to Typst as `--root`.
- Request file keys cannot escape or ambiguously address that project.
- Render concurrency, queue time, compiler time, request sizes and output sizes are bounded.
- Each Typst process runs under a heap limit where the deployment configures one, so a template
  that allocates without bound fails against that limit rather than the host's OOM killer. The
  bound is per process, not per service, and covers the heap and anonymous mappings only —
  memory-mapped font files and temporary scratch space lie outside it.
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

Prelum does not restrict WebAssembly plugins, and cannot: Typst identifies a plugin from its magic
bytes rather than its filename, so a module named `mod.dat` loads exactly as `mod.wasm` does. An
earlier check on the `.wasm` suffix was removed because it stopped only the honest spelling while
every caller had to mirror it.

Nothing is lost by that. A plugin runs in Typst's own sandbox with no host access, and a caller that
can submit source can already run arbitrary Typst code; the bounds that matter are the render
timeout and the per-render memory limit, which apply to plugins as to everything else. Deploy Prelum
only where compiling authenticated caller source is acceptable.

## Error privacy

Validation responses omit Pydantic's mirrored `input` and `ctx`. Caller-controlled values are
handled according to the field:

- `files` keys appear whole in `context.key` only after validation has bounded their characters and
  length;
- object keys in `data` are shortened before appearing in `context.path`;
- validation locations in `context.errors[].loc` and messages in `context.errors[].msg` are also
  shortened, because Pydantic may include values that have not passed key validation;
- `data` values are never returned;
- configured limits such as `limit` and `timeout_secs` may be returned because they are already
  public in the error detail and are not secrets.

Compiler stdout and stderr are neither returned nor logged. Logs and Sentry receive `context` as
one structured field and must not receive request bodies, credentials, proprietary templates or
generated artefacts. Sentry never attaches request bodies, and Prelum redacts the API token header
before an event leaves the process because the SDK's built-in list does not know Prelum's custom
header name.
