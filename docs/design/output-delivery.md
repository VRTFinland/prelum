# Output Delivery Design

**Date:** 2026-08-31  
**Status:** Proposed  
**Scope:** Prelum `POST /v1/render`

## Background

Prelum returns the rendered artefact in the response body. That is the only thing it can do with
it. A caller that wants the artefact in object storage must receive the whole body, buffer it, and
upload it themselves, so the bytes cross the network twice and the caller carries transfer code
Prelum could carry once.

The render path is already shaped for a second destination. `TypstRenderer.render` returns a
`RenderResult` of `bytes`, `content_type`, `filename` and `disposition`, and nothing below the
route knows where those bytes go. The route is the sole place that decides they become an HTTP
body.

This design adds one alternative destination: an HTTP `PUT` to a target the operator has
configured or the caller has authorised. It deliberately introduces no service SDKs. S3, Azure
Blob Storage and SharePoint all accept a pre-authorised `PUT`, so one transport reaches all three
without Prelum holding a credential for any of them.

## Goals

- Deliver the rendered artefact to an HTTP target instead of returning it in the response body.
- Support two target kinds behind one caller-facing concept: operator-configured named sinks, and
  a caller-supplied pre-signed URL.
- Keep the response type knowable from the request: a request with `delivery` always answers JSON,
  a request without it always answers the artefact.
- Reject an unusable delivery target before spending a render.
- Bound delivery in attempts and in wall-clock time, and never hold a render slot while uploading.
- Fail closed: no caller-supplied target is reachable unless an operator has allowed its host.
- Never place a credential — a pre-signed query string, an authorisation header — into a response,
  a log line or a Sentry event.
- Preserve every existing contract: the direct body response, error codes, load shedding,
  authentication, and the single render path.
- Deliver test-first, in three separately verifiable phases.

## Non-goals

- Native S3, Azure or Microsoft Graph clients. The sink registry is built to accept them later;
  this design adds none.
- Asynchronous render jobs, a job store, polling endpoints, or webhooks. Delivery happens inside
  the request.
- Delivering more than one artefact per request. Multi-page image output already resolves to a
  single ZIP; delivery inherits that.
- Multipart or chunked uploads. An artefact is bounded by `PRELUM_MAX_OUTPUT_BYTES` and is sent
  in one `PUT`.
- Reading from a destination, listing it, deleting from it, or checking whether an object exists.
- Signing URLs on the caller's behalf.

## Phase 1: extract the render pipeline

`app/api/routes.py::render` is 90 lines that own queue admission, the semaphore, four metric
families, seven exception branches and response construction. Delivery adds a step with its own
timeout, its own retry loop and its own failure taxonomy. Adding it to that function produces
something no reviewer can hold in their head, and the permit-release invariant recorded in
`AGENTS.md` lives in exactly those lines.

Phase 1 therefore moves the orchestration into `app/render/pipeline.py` and changes no behaviour:

```python
class RenderPipeline:
    def __init__(self, renderer: TypstRenderer, semaphore: asyncio.Semaphore, settings: Settings) -> None: ...

    async def execute(self, job: RenderJob) -> RenderResult:
        """Admit, render and record. Raises the same errors the route raises today."""
```

The pipeline owns:

- bounded queue admission and `ServiceOverloadedError` with its jittered `Retry-After`;
- semaphore acquisition and release on every path, including cancellation;
- `render_queue_waiting`, `concurrent_renders`, `render_duration_seconds`, `render_total`,
  `render_errors_total`, `render_timeouts_total` and `output_size_bytes`;
- classification of unexpected exceptions into `ServiceUnavailableError`.

The route keeps only what belongs to HTTP: dependency injection, building the `RenderJob`, and
turning a `RenderResult` into a `Response`.

**The proof that Phase 1 changed nothing is that the existing tests pass unchanged.** No test is
edited in this phase. `tests/test_load_shedding.py` in particular pins the permit-release
invariant and must stay green without modification. Phase 1 is committed on its own.

## Phase 2: the delivery layer

### Request contract

`delivery` is a new optional top-level field on `RenderRequest`, beside `output`. It describes
where the artefact goes; `output` continues to describe what the artefact is. Omitting it
preserves today's behaviour exactly.

The contract below is stated whole, because the model must be designed for both target kinds at
once. Only the `url` kind is implemented in Phase 2: until Phase 3 lands, a `sink` name resolves
against an empty registry and fails with `invalid_delivery_target`, which is the same answer an
unknown name gets afterwards.

```json
{
  "source": "#text(\"hello\")",
  "output": { "format": "pdf", "filename": "invoice-2026-08.pdf" },
  "delivery": { "sink": "archive", "key": "invoices/2026/08/invoice-2026-08.pdf" }
}
```

```json
{
  "source": "#text(\"hello\")",
  "output": { "format": "pdf" },
  "delivery": { "url": "https://acme.s3.eu-north-1.amazonaws.com/x.pdf?X-Amz-Signature=..." }
}
```

The `Delivery` model is strict in the same way every other request model is (`extra="forbid"`,
frozen):

| Field | Required | Behaviour |
| --- | ---: | --- |
| `sink` | one of | Name of an operator-configured sink |
| `url` | one of | Absolute pre-signed target URL supplied by the caller |
| `key` | no | Object path within a named sink; forbidden with `url` |
| `headers` | no | Extra request headers; each name must appear in the operator's allowlist |

Exactly one of `sink` and `url` must be present; supplying both, or neither, is
`invalid_request`. `key` is only meaningful for a named sink because a pre-signed URL already
names its object. When `key` is omitted, it defaults to the sanitised `output.filename`, and when
that is also absent, to the renderer's generated filename.

A named sink's static headers and the caller's `headers` are merged, with the sink's winning: an
operator who pinned a header on the destination is stating a fact about that destination, not a
default for callers to revise.

`headers` exists for one concrete reason: Azure Blob Storage rejects a `PUT` that lacks
`x-ms-blob-type: BlockBlob`. It is not a general escape hatch. A header name that is not in
`PRELUM_DELIVERY_ALLOWED_HEADERS` is rejected with `invalid_request`, and that setting is empty by
default, so a deployment that has not opted in accepts no caller headers at all. `Content-Type`,
`Content-Length` and `Host` are set by Prelum and may never be overridden, whatever the allowlist
says.

### Response contract

A delivered render answers `200` with `application/json`:

```json
{
  "delivery": {
    "sink": "archive",
    "location": "https://acme.blob.core.windows.net/reports/invoices/2026/08/invoice-2026-08.pdf",
    "filename": "invoice-2026-08.pdf",
    "content_type": "application/pdf",
    "bytes": 128432,
    "sha256": "9f2b1c...e04a"
  }
}
```

The artefact bytes are not returned, in whole or in part, and there is no fallback that returns
them: a request carrying `delivery` answers JSON or it answers an error. This is what makes the
response type predictable from the request.

`location` is the target with its query string removed. A pre-signed query string is a bearer
credential; echoing it would copy that credential into the caller's logs, any proxy in between,
and any error-reporting sink the caller uses. For a caller-supplied URL, `sink` is reported as the
literal string `"url"`.

`sha256` is over the delivered bytes, so the caller can verify the object it later reads is the
one Prelum produced.

### Target validation happens before rendering

A delivery target is resolved and checked as part of request handling, before the pipeline admits
the job. An unknown sink name, a disallowed host, a non-`https` scheme or an unusable key fails
with `invalid_delivery_target` (400) without ever forking a Typst process. A render is the
expensive part of this service; spending one to discover that the caller's URL is malformed is
waste the caller pays for twice.

### Security rules

Prelum is being given the ability to send caller-influenced bytes to a caller-influenced host.
That is a server-side request forgery primitive unless it is constrained, so the constraints are
load-bearing:

- **The host allowlist is the trust boundary.** A caller-supplied URL is accepted only if its host
  matches `PRELUM_DELIVERY_ALLOWED_HOSTS` — an exact host, or a leading-dot entry matching that
  suffix. The setting is empty by default, which means caller-supplied URLs are refused outright
  and only named sinks work. Fail closed is the unconfigured state.
- **`https` only.** `http` is accepted only when `Settings.is_development` is true, and never
  otherwise.
- **The port must be the scheme default.** A `PUT` to `https://allowed.example:22` is refused.
- **Redirects are never followed.** A `3xx` from the destination is a delivery failure, not an
  instruction. Following one would let a permitted host redirect Prelum to a forbidden one.
- **Resolved addresses must be global.** The host is resolved and the request refused if any
  resolved address is loopback, link-local, private, reserved or multicast. This is defence in
  depth, not the boundary: resolution and connection are separate events, and a name that resolves
  differently between them defeats it. The allowlist is what actually confines the destination,
  which is why an empty allowlist denies rather than permits.
- **No credential reaches an observable surface.** The query string and every request header are
  excluded from log events, error details and Sentry events. Only the redacted `location` is ever
  emitted.

Named sinks are not subject to the host allowlist: an operator who wrote the URL template into the
environment has already chosen the destination.

### Delivery runs outside the render semaphore

The render permit is released before delivery begins. A permit exists to cap concurrent Typst
processes, and an upload is not one; holding it across a network round trip would shrink render
capacity in proportion to how slow the destination is. The artefact is already fully in memory at
that point, so nothing is retained by releasing early.

The consequence is explicit: concurrent deliveries are bounded by the HTTP server's own
concurrency and by `PRELUM_MAX_OUTPUT_BYTES`, not by `PRELUM_MAX_CONCURRENT_RENDERS`.

### Retry and failure model

Delivery is one `PUT`, retried within a budget. `PUT` is idempotent, so a retry cannot produce a
second object; a retry that lands after a silent success overwrites identical bytes.

Retried: connection errors, read timeouts, `429`, and `5xx`. A `Retry-After` on a `429` is honoured
when it fits inside the remaining budget and treated as budget exhaustion when it does not.
Backoff is exponential with jitter, following the reasoning already recorded for
`_retry_after_secs`: an unjittered schedule returns every shed caller at the same instant.

Not retried: any other `4xx`. A `403` on a pre-signed URL means expired or wrong, and repeating it
only burns the caller's budget.

The whole of delivery — every attempt and every wait between them — is bounded by
`PRELUM_DELIVERY_TIMEOUT_SECS`. It is a separate budget from the render timeout, deliberately: a
document that took the full render budget must not thereby lose its delivery budget.

Four outcomes, four published codes:

| Condition | Status | Code | Server fault |
| --- | ---: | --- | ---: |
| Unknown sink, disallowed host or scheme, unusable key, disallowed header | 400 | `invalid_delivery_target` | no |
| Destination returned `4xx` other than `429` | 400 | `delivery_rejected` | no |
| Connection failure or `5xx` after attempts are exhausted | 502 | `delivery_failed` | yes |
| Delivery budget exhausted | 504 | `delivery_timeout` | yes |

`delivery_rejected` is a 400 on purpose. The destination refused the caller's own credential;
`status_is_server_fault` treats everything from 500 up as worth paging for, and an expired
pre-signed URL should wake nobody. Its `detail` carries the destination's status code and the
redacted location, never its response body — that body is written by a host the caller chose, and
reflecting it would let that host place arbitrary text into Prelum's error responses.

A failed delivery discards the artefact. There is no partial success: the caller receives an
error, no bytes, and the knowledge that a fresh request is safe to make.

### Client disconnect

Once the `PUT` is in flight it is shielded from cancellation. If the caller disconnects mid-upload,
Prelum completes the delivery rather than abandoning it. A destination left holding a truncated
object is a worse outcome than a completed upload whose receipt nobody read, and the caller can
discover the object by the key they chose.

### Module structure

```
app/delivery/__init__.py     Sink protocol, resolve_target, the registry
app/delivery/http_put.py     The one transport: PUT with retries and redaction
app/delivery/keys.py         Delivery-key validation
```

```python
class Sink(Protocol):
    name: str

    async def put(self, artefact: RenderResult, target: ResolvedTarget) -> DeliveryReceipt: ...
```

Delivery-key validation lives in `app/delivery/keys.py` and not in `app/render/templates.py`.
`AGENTS.md` records that `templates.py` owns the acceptability of `files` keys and that nothing
else decides it. That invariant is about the compiler's input namespace. A delivery key names an
object in someone else's storage and has different rules — no `main.typ` restriction, no `.wasm`
restriction, no relationship to a filesystem Prelum will write. Sharing one validator would mean
one function serving two policies, which is how both drift.

A delivery key must be non-empty, at most 1024 characters, contain no `..` segment, no leading `/`,
no backslash, no control character, no `?`, no `#` and no `%`, and must equal its own normalised
form. `%` is excluded rather than validated: accepting it would mean deciding whether a key is
already encoded, and a key that is encoded once by the caller and again by Prelum names a
different object than either party intended. Prelum percent-encodes the key when substituting it
into the template, so the caller supplies unencoded bytes and gets exactly the object they named.
`?` and `#` are excluded because a template substitution is textual: either one would let a key
terminate the path and rewrite the query the operator's template established. The template substitution is textual, so a key that escapes its
prefix would let a caller write outside the operator's intended area of the bucket.

### Packaging

The transport needs an HTTP client, and `httpx2` is currently a development dependency only.

```toml
[project.optional-dependencies]
delivery = ["httpx2>=2.12.0"]
```

This follows the precedent of the `sentry` extra: an optional vendor dependency with network
access that a deployment not using the feature should not carry. It differs in how it degrades.
`app/core/sentry.py` becomes a no-op when its extra is missing, which is right for error reporting
— losing a report is worse than refusing to start, but only just. Delivery is not observability:
silently ignoring configured sinks would answer 400 to every delivery request in production for a
reason no log explains.

So: if any sink is configured or any host is allowed, and `httpx2` cannot be imported, `Settings`
refuses to start with a message naming the extra. This matches how the service already treats a
missing `PRELUM_API_TOKEN`.

### Configuration

| Setting | Default | Behaviour |
| --- | --- | --- |
| `PRELUM_SINKS` | `{}` | JSON object of named sinks; each has `url_template` and optional static `headers` |
| `PRELUM_DELIVERY_ALLOWED_HOSTS` | `[]` | Hosts accepted in a caller-supplied URL; empty refuses all |
| `PRELUM_DELIVERY_ALLOWED_HEADERS` | `[]` | Caller-settable header names; empty accepts none |
| `PRELUM_DELIVERY_TIMEOUT_SECS` | `30.0` | Whole-delivery budget, attempts and backoff included |
| `PRELUM_DELIVERY_MAX_ATTEMPTS` | `3` | Attempts per delivery, the first one included |

```bash
PRELUM_SINKS='{"archive": {"url_template": "https://acme.blob.core.windows.net/reports/{key}", "headers": {"x-ms-blob-type": "BlockBlob"}}}'
```

`url_template` must contain `{key}` exactly once and must be a valid absolute `https` URL once a
key is substituted. Both are checked at startup, not per request: a template that cannot produce a
URL is a misconfiguration to surface on deploy, not on the first delivery.

### Observability

```python
delivery_total = Counter("prelum_delivery_total", ..., ["sink", "status"])
delivery_duration_seconds = Histogram("prelum_delivery_duration_seconds", ..., ["sink"])
delivery_attempts_total = Counter("prelum_delivery_attempts_total", ..., ["sink", "outcome"])
```

The `sink` label is a configured sink name or the literal `"url"`. It is never a hostname: hosts
come from caller input and would make the label set unbounded, which is how a metrics backend is
brought down by a client.

`render_total` continues to report `success` for a render that completed, whatever delivery then
did. The two are separate outcomes and conflating them would hide a working renderer behind a
broken destination.

## Phase 3: named sinks from configuration

Phase 2 delivers the transport and the caller-supplied URL path. Phase 3 adds `PRELUM_SINKS`
parsing, startup validation of templates, static per-sink headers, and the registry lookup that
turns `delivery.sink` into a resolved target. Splitting it keeps Phase 2's diff to one transport
and one target kind.

## Testing

Test-first throughout. No test may make a real outbound request; the network is unavailable in CI
and a test that needs it is a test that is skipped.

**Unit, no network.** Target resolution: allowed and disallowed hosts, suffix entries, `http` in
and out of development, non-default ports, unresolvable and private addresses. Key validation:
traversal, absolute keys, control characters, over-length, non-normalised forms. Model validation:
`sink` and `url` together, neither, `key` with `url`, a header outside the allowlist, `delivery`
combined with each output format. Redaction: a receipt and an error built from a URL carrying a
signature contain no part of the query string, asserted against the serialised response.

**Transport, against a local server.** A fixture serving a real HTTP endpoint on a loopback port
drives the retry logic honestly rather than through a mocked client: success; `500` then success;
`500` throughout until attempts are exhausted; `429` with a `Retry-After` inside and outside the
budget; `403` producing exactly one attempt; a `302` refused rather than followed; a server that
delays past the budget. The delivered body is compared byte-for-byte with the rendered artefact,
and the recorded `Content-Type` and `sha256` are checked against it.

Loopback is otherwise forbidden by the address rules, so this fixture requires the settings under
test to allow it explicitly — which is itself the assertion that the rule is enforced by
configuration rather than by accident.

**Integration, with the real Typst CLI.** One end-to-end test per phase-2 path: a PDF rendered and
delivered to the local server returns a receipt whose `bytes` and `sha256` match what the server
received; a multi-page PNG ZIP delivers as one archive; a request without `delivery` still returns
the artefact body unchanged.

**Phase 1 regression.** The whole existing suite passes unmodified after the pipeline extraction.

## Consequences

- `POST /v1/render` gains a second response shape. It remains derivable from the request, so a
  caller that never sends `delivery` cannot observe the change.
- Prelum makes outbound connections for the first time. `PRELUM_ALLOW_TYPST_NETWORK` governs the
  compiler's network access and is untouched by this; delivery is a separate, separately
  configured egress path, and both default to closed.
- A deployment that configures nothing gains nothing and loses nothing.
