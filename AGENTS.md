# Agent Guide (prelum)

Follow these guidelines when working on the Prelum service.

- British English in prose and identifiers; avoid needless files, prefer editing existing ones.
- Use uv for dependencies: `uv sync`.

## Running Checks

### Tests
- Unit tests (no Typst CLI required): `uv run pytest -m "not integration"`
- Full test suite (requires Typst CLI): `uv run pytest`
- Integration tests only: `uv run pytest -m integration`
- Or use Make targets: `make test`, `make test-all`, `make test-integration`

Tests marked `integration` compile a template with the real `typst` binary, so plain `pytest`
**fails** without it on PATH — install Typst or use `make test` to deselect them. Never guard such
a test with `shutil.which("typst")` and skip: CI runs the full suite in an image that has the binary
and the report fonts, and a self-skipping test there reports green while compiling nothing.

### Type Checking
- Run the ty type checker: `make type-check` or `uv run ty check app scripts`

### Linting
- Check linting: `make lint` or `uv run ruff check .`
- Fix linting issues: `make lint-fix` or `uv run ruff check --fix .`
- Format code: `make format` or `uv run ruff format .`

### All Checks
- Run lint + type-check: `make checks`

### Documentation
- Build the public site and generated OpenAPI schema: `make docs-build`
- Preview the site locally: `make docs-serve`
- `docs/api/openapi.json` and `site/` are generated and ignored; never edit or commit them.

## Development

- Start locally: `uv run python -m app` (reads `PRELUM_*` env vars; `PRELUM_ENVIRONMENT=development` supplies the dev token).
- Prelum resolves no templates by name. `examples/` holds a sample request rather than a registry.
- Keep request body sizes small and avoid embedding large base64 files unless increasing
  `PRELUM_MAX_INLINE_FILE_BYTES` explicitly.
- The Typst version is pinned in the `Dockerfile`; an unpinned install makes rendered output depend on the image build date.
- Detailed public contracts belong in `docs/`; keep `README.md` to the overview, quick start and
  links, and keep this file to engineering instructions and load-bearing invariants.

## Comments and docstrings

This codebase explains *why*, at length where the reason is not recoverable from the code. That
licence is the reason the rules below are needed: prose that carries no reason is the cost of the
prose that does, because it trains a reader to skim both.

- Write what the code cannot say. A constraint that came from outside the file — a published
  contract, an upstream library's behaviour, a limit an operator sets — belongs in a comment. What
  the next three lines plainly do does not.
- Say it once, where the decision lives. A rationale repeated at each site that depends on it is
  one rationale to keep correct in several places; state it at the definition and point at that
  name from elsewhere, if anywhere.
- No `:param:` or `:return:` line that restates the signature. Keep one only when it adds what the
  name and annotation cannot: a unit, what `None` means, an encoding, the empty case, or that the
  value reaches a caller. Keep every `:raises:` — exceptions are not in the signature.
- Cross-file pointers rot. Name a symbol rather than a file path and line. Naming a test is fine
  where it tells a maintainer what will fail if they ignore the instruction — the module docstrings
  under `scripts/` do this — but not as evidence for the claim above it.
- Bug histories and hypothetical futures belong in the commit message. The comment states the rule
  that now holds; `git log -S` finds the story if anyone needs it.
- A docstring's first line says what the thing is for. If the paragraph below it restates the
  assertions or the branches that follow, delete the paragraph.

## Inline rendering invariants

`/v1/render` compiles a `source` supplied by the caller, so arbitrary caller input reaches the compiler.
The rules below are load-bearing — every one of them exists because breaking it produced a defect. See `README.md` for the caller-facing contract.

- **There is one render path.** `source` is required, so nothing branches on whether a request is
  inline; do not reintroduce a registry lookup beside it.
- **`templates.py` decides whether `files` keys are acceptable, and nothing else does.**
  `validate_inline_file_key` owns per-key shape: the key equals its own normalised form (so no two keys resolve to
  one file), stays inside the project, is no longer than `MAX_KEY_LENGTH` with no segment over
  `MAX_KEY_SEGMENT_LENGTH`. `validate_inline_file_keys` owns the rules that need the
  whole set — one key used as another's directory, two keys differing only in case — and caps the count at
  `MAX_INLINE_FILE_KEYS` *before* doing any of that work, because it is O(keys × depth). That cap is structural and
  separate from the configurable `PRELUM_MAX_INLINE_FILES`, which the renderer enforces. Both validate with POSIX
  semantics, so the guarantee does not depend on the host filesystem. A new rule goes in one of those two functions;
  the renderer must stay free of key policy.
- **The inline entry point is protected by being unspellable, not by a rule.** `INLINE_TEMPLATE_FILENAME` is
  `~main.typ`, and `~` is deliberately outside `SAFE_FILENAME_CHARS`: the character check in
  `validate_inline_file_key` already refuses every spelling of it, in any letter case and as a leading directory,
  so no key can name the file the inline source is written to last. There is therefore no reserved-name rule and
  nothing reserved in the published document — `main.typ` is an ordinary key. Adding `~` to `SAFE_FILENAME_CHARS`,
  or renaming the constant to something a key could spell, silently restores a collision in which a caller's entry
  loses its bytes to the inline source; `tests/test_templates.py` pins the invariant.
- **The published pattern and the validators are one statement.** `INLINE_FILE_KEY_PATTERN` is the
  caller-facing form of every per-key rule but the denied suffix, and
  `tests/test_inline_file_key_properties.py` pins it to `validate_inline_file_key` over a targeted
  and an untargeted generator. A new rule changes both or neither. `app/core/constraints.py`
  publishes the pattern, the limits and the conformance vectors and holds no rule of its own — a rule
  there and not in `templates.py` is one Prelum does not enforce. Raise `RULES_VERSION` whenever a
  published rule changes, and add a vector for every new whole-set rule or declare the exemption in
  `RULES_WITHOUT_VECTORS`.
- **Queue waiting is bounded, and shedding is not an incident.** `/v1/render` waits at most
  `PRELUM_MAX_QUEUE_WAIT_SECS` for a semaphore permit and then raises `ServiceOverloadedError`
  (429 + `Retry-After`). 429 rather than 503 is deliberate: `ServiceOverloadedError` declares
  `Origin.capacity`, so `is_server_fault` (`origin is Origin.service`) is false for it, and shedding
  load is designed behaviour rather than something worth paging for. The permit
  must be released on every path — a leak would shrink capacity on each shed until nothing was
  served, which is what `tests/test_load_shedding.py` exists to catch.
- **The renderer guarantees no 5xx from caller input.** `_write_inline_files` maps layout errnos
  (`EEXIST`/`EISDIR`/`ENOTDIR`/`ENAMETOOLONG`) to `InvalidFileDataError` as a backstop for what only the filesystem can
  refuse, but deliberately lets everything else (`ENOSPC`, `EACCES`) through as a server fault — do not widen that
  to a blanket `except OSError`.
- **Classify by whose fault it is.** Caller input fails with a 4xx (`InlineTemplateError` = 422 for a compile
  failure, `InvalidFileDataError`/`InvalidTemplatePathError` = 400); our own infrastructure fails with a 5xx. Anything that is not an `AppError` becomes a 503 with an `error` log and a Sentry event, so a bare
  exception escaping the renderer is a bug even when the request was nonsense.
- **Encode caller text through `_encode_utf8`.** JSON can carry lone surrogates, which have no UTF-8 encoding;
  in `data` values they are stripped by the `_TYPST_ESCAPES` table alongside the direction overrides. That table
  is the single statement of what is unsafe inside a Typst string literal — add escapes there, not to a branch.
- **Each error class declares its own `Origin`, and that declaration decides what counts as an incident.**
  `AppError.is_server_fault` is exactly `origin is Origin.service`, and both the error handler and the
  renderer's compile-failure logging read `is_server_fault` off the instance. One declaration therefore
  drives both the published `origin` field and the log level. Do not re-derive the 4xx/5xx split from status.
- **The one signal a configured memory bound produces is the caller's fault; every other signal is ours.** Which
  signal that is depends on the configuration, so the rule is conditional and not a fixed set. Bounded by
  `PRELUM_MAX_RENDER_MEMORY_BYTES`, the allocation fails inside Typst and Rust aborts, so it is `SIGABRT` — and a
  `SIGKILL` then came from outside the render (the container's memory limit, an eviction, a supervisor), which is
  usually infrastructure and must stay visible. Usually, not always: `RLIMIT_DATA` covers the heap and anonymous
  mappings, so caller-supplied fonts Typst mmaps and scratch on a tmpfs fall outside it, and on kernels before 4.7
  so does anonymous mmap entirely — a caller can still drive a container-level kill. That is accepted, because no
  classification can separate it from an under-provisioned pod and an occasionally caller-driven 5xx is the safer
  mistake. Unbounded, the kernel's OOM killer sends `SIGKILL`
  for the same runaway template, and `SIGABRT` means Typst aborted on its own. A caller provokes whichever applies
  on demand, so routing it to a 5xx would hand every caller a lever on the alert channel; routing the *other* one
  to a 4xx hides a real incident, because `is_server_fault` (an `Origin.service` declaration) is the only thing
  that raises the log to `error` and so the only path to Sentry. Do not flatten this into an unconditional set of signals — it was that once, and
  it silently swallowed pod-level OOM kills.
- **The memory limit's wrapper argv is load-bearing.** `prlimit`'s `--data` takes an *optional* argument, so
  `("--data", value)` is not equivalent to `f"--data={value}"`: split, getopt leaves the limit unset, `prlimit`
  execs the number and exits 127 — which the renderer reads as a compile failure and reports as 422. The startup
  probe in `app/main.py` exists to catch exactly that class of silent misconfiguration; do not remove it, and do
  not rewrite `resource_limit_args` (module-level in `app/render/renderer.py`, and forked by
  `app/main.py`'s startup probe) into the `extend((flag, value))` idiom the output arguments use.
- **Never mirror the request body into an error or log, and decide echoed values per field.** Validation responses
  drop pydantic's `input` and `ctx`. Prose goes through `for_message`, and so does every caller-supplied segment of
  a validation `loc` and of `context.path`, because neither has passed key validation. `context.key` is the deliberate
  exception: it carries a whole `files` key, which `validate_inline_file_key` has already bounded to
  `MAX_KEY_LENGTH` and a safe character set, because a caller matching it against its own `files` map needs it
  exact. That makes the log line and Sentry the one place a whole key leaves the process, which is accepted —
  see "Error privacy" in `docs/operations/security-model.md`. `data` values have no field and are never returned.
  Typst diagnostics can quote source, so the subprocess keeps stdout and stderr on `DEVNULL`; do not pipe, return
  or log them.
- **OpenAPI authentication and runtime authentication share one dependency.** `/v1/render` uses the
  `PrelumApiToken` `APIKeyHeader` through `Security`, with `auto_error=False` so `require_api_token` retains the
  published constant-time comparison and 403 response. Do not turn it back into an optional ordinary `Header` in
  OpenAPI or enable FastAPI's automatic error response.
- **The network control is the proxy environment in `_typst_env`**, plus `--package-cache-path` pinned to an empty
  per-render directory. `--package-path` uses the same empty directory by default and may only point at the
  operator-configured `PRELUM_LOCAL_PACKAGE_PATH`; that path does not enable downloads. No released Typst version
  has an offline flag. Unit tests pin the arguments because an integration test passes vacuously without egress.
- **Claim only what a control does, and do not keep one that controls nothing.** The `.wasm` suffix denial was
  removed for exactly this reason: Typst identifies a plugin by its magic bytes rather than its filename, so the
  rule stopped only the honest spelling while every caller had to mirror it — including the detail that it matched
  the whole filename, because `Path.suffix` is empty for `.wasm`. Do not reintroduce it. A published rule that
  costs callers work and prevents nothing is worse than no rule, and needing a caveat in the security model is the
  signal that a control is theatre.
- **Page selection has one parser.** PDF `pages` and image-archive `pages` use `parse_page_selection`; the
  renderer must not parse the range syntax again. Its grammar accepts ASCII digits only, matching Typst's CLI, and
  its structural limits are 256 characters and 64 comma-separated entries. `bound_page_selection` converts
  all-page and open-range image exports to at most
  `PRELUM_MAX_OUTPUT_FILES + 1` candidates. The extra candidate is a sentinel: if Typst emits it, fail with
  `too_many_output_files` rather than returning a partial archive.
- **`PRELUM_FONT_PATH` names one directory, not a path list.** Settings validation owns the shared absolute,
  existing and readable directory checks. Its font-specific validator also rejects `os.pathsep`, because the renderer
  joins the project root and configured directory into one `--font-path` value; accepting the separator would turn a
  directory name into additional Typst search roots.
- **Image archives are one bounded render, not a second render path.** Typst receives the owned `output-{p}`
  template once, and only matching regular non-symlink files enter the ZIP. Both their aggregate uncompressed
  size and the final ZIP size use `PRELUM_MAX_OUTPUT_BYTES`. An explicit `page`/`pages` selection that matches
  nothing is caller input and returns 422; do not turn Typst's successful no-output exit into a 5xx.
- **Key rules belong in `tests/test_inline_file_key_properties.py`.** Add the invariant as a property and extend
  `SEGMENTS` with the spelling that would break it; three defects here were found only after the properties
  existed, and each was a missing segment in that pool.
- In renderer tests, `_TYPST_ARG_TEMPLATE`/`_TYPST_ARG_OUTPUT` index from the *end* of the argument list, since
  typst takes input and output last. Counting from the front breaks whenever `_run_typst` gains a flag.
