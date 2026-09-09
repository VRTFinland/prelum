# Configuration

All settings use the `PRELUM_` prefix.

| Name | Default | Purpose |
| --- | ---: | --- |
| `BIND` | `0.0.0.0` | HTTP bind host |
| `PORT` | `9870` | HTTP port |
| `ENVIRONMENT` | unset | Controls development-token use; when unset, token mode uses `SENTRY_ENVIRONMENT` |
| `API_TOKEN` | unset | Shared `X-Prelum-Api-Token`; required outside development |
| `CLI_PATH` | `typst` | Typst executable |
| `FONT_PATH` | unset | One existing absolute font directory; mount read-only |
| `LOCAL_PACKAGE_PATH` | unset | One existing absolute local-package directory; mount read-only |
| `RENDER_TIMEOUT_SECS` | `15` | Per-render timeout |
| `MAX_CONCURRENT_RENDERS` | `10` | Concurrent Typst processes |
| `MAX_QUEUE_WAIT_SECS` | `10` | Maximum wait for a render slot before returning 429 |
| `RETRY_AFTER_SECS` | `5` | Floor for the jittered `Retry-After` response |
| `MAX_REQUEST_BODY_BYTES` | `20971520` | Actual JSON body bytes |
| `MAX_STRING_BYTES` | `1048576` | One string in `data` |
| `MAX_TEMPLATE_SOURCE_BYTES` | `524288` | UTF-8 source bytes |
| `MAX_INLINE_FILE_BYTES` | `1048576` | One file after decoding |
| `MAX_INLINE_FILES` | `64` | Files per request; structural validation has a fixed ceiling of 1024 |
| `MAX_OUTPUT_FILES` | `64` | PNG or SVG files in one ZIP response |
| `MAX_OUTPUT_BYTES` | `52428800` | Direct output, aggregate images and completed ZIP |
| `ALLOW_TYPST_NETWORK` | `false` | Permit Typst package downloads |
| `DEBUG_OUTPUT_DIR` | unset | Save bounded debug copies |
| `LOG_PRETTY` | `false` | Human-readable logs |
| `SENTRY_DSN` | unset | Sentry DSN; requires the `sentry` extra |
| `SENTRY_ENVIRONMENT` | unset | Environment reported to Sentry |
| `SENTRY_TRACES_SAMPLE_RATE` | unset | Sentry trace sample rate from 0 to 1 |

`FONT_PATH` must not contain the platform path-list separator (`:` in the published Linux image),
because it names one directory rather than a directory list. Both mounted resource paths must
exist, be absolute and be readable when the service starts.

The Sentry SDK is optional. Install it with `uv sync --extra sentry`; the published image already
includes it. Setting `SENTRY_DSN` without the extra logs `sentry.sdk_missing` at error level but does
not stop the service.
