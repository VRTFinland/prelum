# Configure Prelum

Prelum reads its configuration from environment variables beginning with `PRELUM_`. The defaults
are suitable for trying the service locally; a deployed service must at least set a private API
token.

For example:

```bash
PRELUM_API_TOKEN=replace-with-a-secret \
PRELUM_PORT=9870 \
uv run python -m app
```

Invalid configuration stops the service during startup, before it accepts requests.

## Authentication and listener

| Variable | Default | What it controls |
| --- | ---: | --- |
| `PRELUM_API_TOKEN` | unset | Shared value required in the `X-Prelum-Api-Token` request header |
| `PRELUM_ENVIRONMENT` | unset | Enables the public development token only for `development`, `local`, `dev` or `test` |
| `PRELUM_BIND` | `0.0.0.0` | Address on which the HTTP server listens |
| `PRELUM_PORT` | `9870` | HTTP port |

Outside the four named development environments, `PRELUM_API_TOKEN` is required. When
`PRELUM_ENVIRONMENT` is unset, token mode falls back to `PRELUM_SENTRY_ENVIRONMENT`; set the
environment explicitly if that fallback would be surprising.

## Capacity and size limits

Lower these values to match the memory, CPU and request limits of the surrounding deployment.
Byte limits measure the actual UTF-8, decoded file or generated output bytes described below.

| Variable | Default | What is limited |
| --- | ---: | --- |
| `PRELUM_RENDER_TIMEOUT_SECS` | `15` | Runtime of one Typst process |
| `PRELUM_MAX_CONCURRENT_RENDERS` | `2` | Typst processes running at the same time |
| `PRELUM_MAX_QUEUE_WAIT_SECS` | `10` | Wait for a render slot before returning 429 |
| `PRELUM_RETRY_AFTER_SECS` | `5` | Floor for the jittered `Retry-After` response |
| `PRELUM_MAX_REQUEST_BODY_BYTES` | `20971520` | Complete JSON request body |
| `PRELUM_MAX_TEMPLATE_SOURCE_BYTES` | `524288` | UTF-8 bytes in `source` |
| `PRELUM_MAX_INLINE_FILES` | `64` | Files in one request; structural validation has a fixed ceiling of 1024 |
| `PRELUM_MAX_INLINE_FILE_BYTES` | `1048576` | One file after text encoding or Base64 decoding |
| `PRELUM_MAX_STRING_BYTES` | `1048576` | One string or object key in `data` |
| `PRELUM_MAX_OUTPUT_FILES` | `64` | PNG or SVG pages in one ZIP |
| `PRELUM_MAX_OUTPUT_BYTES` | `52428800` | Direct output, aggregate images and completed ZIP |
| `PRELUM_MAX_RENDER_MEMORY_BYTES` | `536870912` in the image, otherwise unset | Heap of one Typst process |

`PRELUM_MAX_RENDER_MEMORY_BYTES` applies `RLIMIT_DATA` to each Typst process, so a template that
allocates without bound fails against its own limit instead of the container's OOM killer — which
chooses its victim by `oom_score` and may pick the service rather than the render that caused it.
It is applied through the `prlimit` wrapper from util-linux, which exists only on Linux, so the
setting is unset by default and enabled in the published image. Because neither Docker nor
Kubernetes can remove a variable the image sets, an empty value counts as unset — set
`PRELUM_MAX_RENDER_MEMORY_BYTES=` to run the image somewhere the wrapper is unavailable. When it is
set, the service probes
the wrapper at startup and refuses to serve if it cannot honour the value; that is deliberate,
because every way the wrapper can fail exits the same way a failed compilation does, and would
otherwise answer `422` to every caller for what is a deployment fault. The floor is 128 MiB: a
document of a few hundred sections needs more than 64 MiB, so a lower limit would fail legitimate
renders the same way it fails runaway ones.

### Sizing the container

`PRELUM_MAX_CONCURRENT_RENDERS` multiplies almost every other memory cost, which is why it defaults
to 2 rather than something larger. Each render in flight holds three things at once:

| Per render in flight | Default | Bounded by `RLIMIT_DATA`? |
| --- | ---: | --- |
| The Typst process's heap | 512 MiB | yes |
| The parsed request body | up to 20 MiB | no — it is this service's memory |
| The finished output, held whole before it is sent | up to 50 MiB | no — same |

At the defaults that is roughly 1.2 GiB of ceiling plus the interpreter's own baseline, so about
2 GiB is a sensible container limit. Give the container less and its OOM killer fires before any
render reaches its own limit, and it need not pick the render that caused the pressure. Raising
concurrency without raising the container's memory in step is the most common way to reintroduce
exactly the failure `PRELUM_MAX_RENDER_MEMORY_BYTES` exists to prevent.

Note also what `RLIMIT_DATA` does not reach: it bounds the heap and anonymous mappings of the Typst
process only. Memory-mapped font files are outside it, as is the scratch space under
`PRELUM_TEMP_ROOT` when that path is memory-backed (see below).

Authenticated clients can read the effective public limits from `GET /v1/constraints`. This lets
them provide early feedback without copying deployment configuration; see
[Constraints and files-key rules](../api/files-key-rules.md).

## Typst and shared resources

| Variable | Default | What it controls |
| --- | ---: | --- |
| `PRELUM_CLI_PATH` | `typst` | Typst executable |
| `PRELUM_FONT_PATH` | unset | One additional, recursively searched font directory |
| `PRELUM_LOCAL_PACKAGE_PATH` | unset | Directory containing versioned local Typst packages |
| `PRELUM_ALLOW_TYPST_NETWORK` | `false` | Whether Typst may download remote packages |
| `PRELUM_TEMP_ROOT` | `/tmp` | Directory each render builds its temporary project in |

`PRELUM_TEMP_ROOT` must be an existing, writable absolute directory, and it must be short: the
published files-key length bound is derived from how much of the operating system's path limit the
render root leaves free, so a long root is rejected at startup rather than accepted and then found
wanting on the first request. A container run with `--read-only` needs `--tmpfs /tmp` or a writable
volume mounted here; a memory-backed `/tmp` charges render scratch space against the container's
memory limit, which is a reason to point this at a disk-backed path instead.

Font and package paths must be existing, readable absolute directories and should be mounted
read-only. `PRELUM_FONT_PATH` names one directory, not a path list, so it must not contain the
platform path-list separator (`:` in the published Linux image).

Keep network access disabled unless templates intentionally import remote packages. See
[Fonts and local packages](fonts-and-packages.md) for mount layouts and the
[security model](security-model.md) for the network boundary.

## Logs, diagnostics and Sentry

| Variable | Default | What it controls |
| --- | ---: | --- |
| `PRELUM_LOG_PRETTY` | `false` | Human-readable rather than structured JSON logs |
| `PRELUM_DEBUG_OUTPUT_DIR` | unset | Directory for bounded render debug copies |
| `PRELUM_SENTRY_DSN` | unset | Sentry project DSN |
| `PRELUM_SENTRY_ENVIRONMENT` | unset | Environment reported to Sentry |
| `PRELUM_SENTRY_TRACES_SAMPLE_RATE` | unset | Trace sample rate from 0 to 1 |

The Sentry SDK is optional when running from source. Install it with `uv sync --extra sentry`; the
published image already includes it. Setting `PRELUM_SENTRY_DSN` without the extra logs
`sentry.sdk_missing` at error level but does not stop the service.
