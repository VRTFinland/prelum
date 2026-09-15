# Check request limits before rendering

`GET /v1/constraints` tells a client what one Prelum deployment accepts in a render request. Use it
to check file names and request sizes before uploading a project, so a user receives immediate
feedback instead of waiting for `POST /v1/render` to reject the request.

You can skip this endpoint in a simple integration: `POST /v1/render` always validates the complete
request and returns a structured error. Constraints are useful when building an upload form, giving
early feedback for large projects or implementing a reusable client library.

Fetch the constraints once when the client starts and refresh them when its Prelum deployment or
configuration changes. There is no need to call the endpoint before every render.

## Make a request

The endpoint has no request body. Send the same API token that you use for rendering:

```bash
curl --silent --show-error http://localhost:9870/v1/constraints \
  -H "X-Prelum-Api-Token: dev-only-insecure-token" \
  --output prelum-constraints.json
```

A successful response is JSON. It contains two kinds of information:

- **key rules** describe valid names in the `files` object and are the same for every deployment
  running that version of Prelum;
- **limits** describe sizes and counts accepted by this particular deployment and may differ
  between environments.

Use `jq` to inspect the fields that most clients need first:

```bash
jq '{rules_version, max_keys, limits}' prelum-constraints.json
```

With the default development configuration, the result is:

```json
{
  "rules_version": 5,
  "max_keys": 1024,
  "limits": {
    "effective_max_files": 64,
    "max_inline_files": 64,
    "max_inline_file_bytes": 1048576,
    "max_template_source_bytes": 524288,
    "max_string_bytes": 1048576,
    "max_request_body_bytes": 20971520,
    "max_output_bytes": 52428800,
    "max_output_files": 64
  }
}
```

The endpoint returns `403 Forbidden` if the token is missing or incorrect.

## Use the response

For a client that sends `source`, `files` and `data` to `POST /v1/render`, the usual checks are:

- encode `source` as UTF-8 and keep it within `limits.max_template_source_bytes`;
- keep the number of `files` entries within `limits.effective_max_files`;
- validate every `files` key using the rules described below;
- keep each decoded file within `limits.max_inline_file_bytes`;
- keep each string in `data` within `limits.max_string_bytes`;
- keep the complete JSON request within `limits.max_request_body_bytes`.

For example, these keys can be used directly in a render request:

| Key | Accepted | Reason |
| --- | --- | --- |
| `assets/logo.png` | yes | Normalised relative path with safe characters |
| `lib/report.typ` | yes | Nested relative path |
| `main.typ` | yes | No filename is reserved |
| `../secret.txt` | no | Leaves the project directory |
| `/etc/passwd` | no | Absolute path |
| `assets//logo.png` | no | Empty path segment |

An accepted key becomes the path that Typst uses inside the temporary project. This request makes
the image available to the source as `assets/logo.png`:

```json
{
  "source": "#image(\"assets/logo.png\")",
  "files": {
    "assets/logo.png": {
      "encoding": "base64",
      "content": "<base64-encoded PNG>"
    }
  },
  "output": {
    "format": "pdf"
  }
}
```

The endpoint also returns `conformance_vectors`: ready-made accepted and rejected key sets. SDK
authors can run these cases against their local validator to prove that it agrees with Prelum.

## Code examples

Most applications should not copy Prelum's complete validator into client code. Use
`/v1/constraints` for a few inexpensive checks that improve feedback, then send the complete
request to `/v1/render`. The render endpoint validates the request authoritatively and returns a
structured problem response if anything is wrong.

### Python 3.14

```python
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

base_url = "http://localhost:9870"
headers = {"X-Prelum-Api-Token": os.environ["PRELUM_API_TOKEN"]}

# Fetch the deployment's limits once when the client starts.
with urlopen(Request(f"{base_url}/v1/constraints", headers=headers), timeout=10) as response:
    constraints = json.load(response)

render_request = {
    "source": '#include "greeting.typ"\n\n#data.name',
    "files": {
        "greeting.typ": {"encoding": "text", "content": "Hello from Prelum!"},
    },
    "data": {"name": "Ada"},
    "output": {"format": "pdf"},
}
limits = constraints["limits"]

# Give immediate feedback for the most useful whole-request limits.
if len(render_request["source"].encode("utf-8")) > limits["max_template_source_bytes"]:
    raise ValueError("The Typst source is too large")
if len(render_request["files"]) > limits["effective_max_files"]:
    raise ValueError("The request contains too many files")

body = json.dumps(render_request, separators=(",", ":")).encode("utf-8")
if len(body) > limits["max_request_body_bytes"]:
    raise ValueError("The complete request is too large")

try:
    request = Request(
        f"{base_url}/v1/render",
        data=body,
        method="POST",
        headers={**headers, "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        Path("result.pdf").write_bytes(response.read())
except HTTPError as error:
    problem = json.load(error)
    raise ValueError(f"Prelum rejected the request: {problem['detail']}") from error
```

The important part is that `/v1/render` receives and validates the complete `render_request`.
Constraints provide faster local feedback but do not replace that validation.

### TypeScript

```typescript
import { writeFile } from "node:fs/promises"

type Constraints = {
  limits: {
    effective_max_files: number
    max_request_body_bytes: number
    max_template_source_bytes: number
  }
}

const baseUrl = "http://localhost:9870"
const apiToken = process.env.PRELUM_API_TOKEN
if (!apiToken) throw new Error("PRELUM_API_TOKEN is required")

const headers = { "X-Prelum-Api-Token": apiToken }
const constraintsResponse = await fetch(`${baseUrl}/v1/constraints`, { headers })
if (!constraintsResponse.ok) throw new Error("Could not fetch Prelum constraints")
const constraints = (await constraintsResponse.json()) as Constraints

const renderRequest = {
  source: '#include "greeting.typ"\n\n#data.name',
  files: {
    "greeting.typ": { encoding: "text", content: "Hello from Prelum!" },
  },
  data: { name: "Ada" },
  output: { format: "pdf" },
}
const limits = constraints.limits

if (Buffer.byteLength(renderRequest.source, "utf8") > limits.max_template_source_bytes) {
  throw new Error("The Typst source is too large")
}
if (Object.keys(renderRequest.files).length > limits.effective_max_files) {
  throw new Error("The request contains too many files")
}

const body = JSON.stringify(renderRequest)
if (Buffer.byteLength(body, "utf8") > limits.max_request_body_bytes) {
  throw new Error("The complete request is too large")
}

const response = await fetch(`${baseUrl}/v1/render`, {
  method: "POST",
  headers: { ...headers, "Content-Type": "application/json" },
  body,
})
if (!response.ok) {
  const problem = (await response.json()) as { detail: string }
  throw new Error(`Prelum rejected the request: ${problem.detail}`)
}
await writeFile("result.pdf", Buffer.from(await response.arrayBuffer()))
```

This is normally enough for an application integration. SDK authors who need an exact client-side
mirror can implement the per-key and whole-set rules below, then verify their implementation with
`conformance_vectors`.

## Response fields

| Field | Meaning |
| --- | --- |
| `rules_version` | Changes when the published key rules change |
| `key_pattern` | A PCRE-form expression covering the per-key rules |
| `max_keys` | Structural upper bound for the number of keys |
| `set_rules` | Rules that compare two or more keys |
| `conformance_vectors` | Accepted and rejected examples for testing a local validator |
| `limits` | Limits configured for the deployment that answered the request |

The static [`files-key-rules.json`](files-key-rules.json) file contains the same key rules for tools
that cannot contact a running service. It does not contain `limits`, because a static document
cannot describe an individual deployment.

The response also carries `output_rules`, the matching contract for the `output` object, under its
own version counter. [Choose an output format](output-formats.md) describes it, and it is published
statically as [`output-rules.json`](output-rules.json).

## Reference: per-key shape

Every per-key rule is published twice: once as data, and once folded into `key_pattern` for the
engines that can compile it. The data is the contract. Split a key on `/` and require of every
segment that it is:

- at least `min_segment_length` and at most `max_segment_length` characters — the minimum is what
  rejects `lib//label.typ`, `lib/` and `/lib/x.typ`, each of which splits into an empty segment;
- built only from characters matching `segment_character_class`;
- not made only of dots, while `segments_may_not_be_only_dots` is true — `.`, `..` and `...` are all
  rejected.

Then one rule about the key as a whole: it must be between `min_key_length` and `max_key_length`
characters once joined. That is the one rule a per-segment check cannot see — what reaches the
filesystem is the absolute project root plus the key, and `max_key_length` is what remains after the
room reserved for that root.

That is the whole per-key contract, in any language, with no regular expression involved. It is also
exactly what Prelum itself does — the service validates structurally and publishes the expression,
not the other way round.

No filename is reserved. `main.typ` is an ordinary key, at the root as much as anywhere else:
Prelum writes your source to a name that no valid key can spell, so there is nothing for a key to
collide with.

### The expression

`key_pattern` folds the three segment rules into one anchored expression. Restricting the character
set makes separate absolute-path and normalisation checks unnecessary: a leading `/`, `//`, `./`, a
trailing `/` and `..` all fail it.

It begins with `\A` and ends with `\z`, not `^` and `$`. In PCRE, Java and Python `$` also matches
before a trailing newline, and in Ruby both `^` and `$` are line anchors whatever flags you pass, so
a mirror anchored with either accepts `lib/label.typ\n` or `bad\nlib/label.typ` while Prelum rejects
both. A conformance vector covers each end.

`key_pattern_flavour` is `pcre`. Java, .NET, PHP, Ruby and Python 3.14 or later take it as written.
Other common runtimes need an edit or a rewrite:

- **Python 3.13 and earlier.** `\z` was only added to the `re` module in 3.14; before that it raises
  `re.error: bad escape \z`. Replace every `\z` with `\Z`, which in Python — unlike in PCRE and Java
  — already means the absolute end of the string. Do not substitute `$`.
- **JavaScript.** There is neither `\A` nor `\z`. Without the `m` flag, `^` is an absolute start
  anchor, but `$` may still match before a final line break. Replace the leading `\A` with `^` and
  every `\z` with `(?![\s\S])`, a negative lookahead that can only succeed at the absolute end.
- **Go and Rust.** `regexp` and `regex` are RE2, which has no lookahead, so `(?!...)` does not
  compile and the expression is unusable at all. Apply the four rules above as code instead; nothing
  is lost, because each one is published as data.

The `\z` occurrences are the two inside the dot-segment lookaheads and the one inside the length
lookahead as well as the final anchor, so replace all of them, not only the last.

Whichever route you take, the conformance vectors are what tell you it is equivalent.

## Whole-set rules

Three rules need the whole mapping and are published as `set_rules` rather than as data:

- `too_many_keys` — at most `max_keys` entries, checked first because the other two are O(keys × depth).
- `case_collision` — no two keys whose case-folded forms are equal.
- `ancestor_collision` — no key whose case-folded form equals a case-folded POSIX ancestor of another
  key. Fold case on both sides: `lib` and `LIB/x.typ` collide.

## How many files

`max_keys` (1024) is structural and never changes with configuration. `limits.max_inline_files` is
the deployment's own cap and is often lower — 64 by default. The effective cap is the smaller of the
two, reported as `limits.effective_max_files`. Mirroring only one of them gets the contract wrong in
one direction or the other.

## Conformance vectors

`conformance_vectors` is executable, not illustrative. Each entry gives a list of keys, whether
Prelum accepts them, the problem `code` if not, and the `set_rules` id where one applies. Prelum runs
every vector against its own validators in CI, so a vector cannot describe behaviour the service does
not have.

Run them against your mirror in your own CI. That, rather than reading the rules, is what keeps a
mirror from drifting.

`rules_version` rises whenever any published rule changes. Pin it, and treat a change as a signal to
re-run the vectors.

The document itself may gain fields within the same API version, so parse it leniently: ignore
members you do not recognise rather than rejecting the response. A strict validator generated from
the schema would otherwise fail on an ordinary release. See
[Fields may be added](errors.md#fields-may-be-added) for the rule and what it excludes.
