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

## Inline rendering invariants

`/v1/render` compiles a `source` supplied by the caller, so arbitrary caller input reaches the compiler.
The rules below are load-bearing — every one of them exists because breaking it produced a defect. See `README.md` for the caller-facing contract.

- **There is one render path.** `source` is required, so nothing branches on whether a request is
  inline; do not reintroduce a registry lookup beside it.
- **`templates.py` decides whether `files` keys are acceptable, and nothing else does.**
  `validate_inline_file_key` owns per-key shape: the key equals its own normalised form (so no two keys resolve to
  one file), stays inside the project, has no segment over `MAX_KEY_SEGMENT_LENGTH`, is not `main.typ` as a leading
  segment in any letter case, and is not a `.wasm` file. `validate_inline_file_keys` owns the rules that need the
  whole set — one key used as another's directory, two keys differing only in case — and caps the count at
  `MAX_INLINE_FILE_KEYS` *before* doing any of that work, because it is O(keys × depth). That cap is structural and
  separate from the configurable `PRELUM_MAX_INLINE_FILES`, which the renderer enforces. Both validate with POSIX
  semantics, so the guarantee does not depend on the host filesystem. A new rule goes in one of those two functions;
  the renderer must stay free of key policy.
- **Queue waiting is bounded, and shedding is not an incident.** `/v1/render` waits at most
  `PRELUM_MAX_QUEUE_WAIT_SECS` for a semaphore permit and then raises `ServiceOverloadedError`
  (429 + `Retry-After`). 429 rather than 503 is deliberate: `status_is_server_fault` treats
  everything from 500 up as worth paging for, and shedding load is designed behaviour. The permit
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
- **`status_is_server_fault` decides what counts as an incident**, and both the error handler and the renderer's
  compile-failure logging use it. Do not re-derive the 4xx/5xx split.
- **Never mirror the request body into an error or log.** Validation responses drop pydantic's `input`, and messages
  that quote a caller-supplied path go through `for_message`. Typst diagnostics can quote source, so the subprocess
  keeps stdout and stderr on `DEVNULL`; do not pipe, return or log them.
- **OpenAPI authentication and runtime authentication share one dependency.** `/v1/render` uses the
  `PrelumApiToken` `APIKeyHeader` through `Security`, with `auto_error=False` so `require_api_token` retains the
  published constant-time comparison and 403 response. Do not turn it back into an optional ordinary `Header` in
  OpenAPI or enable FastAPI's automatic error response.
- **The network control is the proxy environment in `_typst_env`**, plus `--package-cache-path` pinned to an empty
  per-render directory. `--package-path` uses the same empty directory by default and may only point at the
  operator-configured `PRELUM_LOCAL_PACKAGE_PATH`; that path does not enable downloads. No released Typst version
  has an offline flag. Unit tests pin the arguments because an integration test passes vacuously without egress.
- **Claim only what a control does.** The `.wasm` check is a speed bump, not a boundary, because Typst identifies
  a plugin by its magic bytes rather than its filename — the README says so, and it must keep saying so.
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
