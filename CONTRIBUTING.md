# Contributing to Prelum

Thank you for considering a contribution to Prelum.

## Before starting

Search the existing issues and pull requests before starting work. Please open an issue before
implementing a substantial feature or changing the public API so that the intended behaviour and
scope can be agreed first.

Keep changes focused. Avoid unrelated refactoring, generated files that the project does not use,
and new dependencies when the existing implementation can reasonably do the job.

## Development setup

Prelum requires Python 3.14 or later and uses [uv](https://docs.astral.sh/uv/) for dependency
management:

```shell
uv sync
```

Start a development instance with the built-in development token:

```shell
PRELUM_ENVIRONMENT=development uv run python -m app
```

The Typst CLI is required to run the service and the integration tests. The Docker build includes
the pinned Typst version and the default report fonts.

## Tests and quality checks

Run the unit tests without the Typst CLI:

```shell
uv run pytest -m "not integration"
```

Run the integration tests or the complete suite when Typst is available on `PATH`:

```shell
uv run pytest -m integration
uv run pytest
```

Integration tests must compile with the real Typst binary. Do not make them silently skip when the
binary is unavailable.

Run linting, type checking and formatting with:

```shell
uv run ruff check .
uv run ty check app
uv run ruff format .
```

Equivalent Make targets include `make test`, `make test-integration`, `make test-all`, `make lint`,
`make type-check`, `make format` and `make checks`.

Run the service from the source tree on `127.0.0.1:9870`, with the development environment and its
published token:

```shell
make serve
```

This uses whichever `typst` is on `PATH`, so it is a development convenience: the container pins the
compiler by digest and remains the supported way to deploy.

Build the public documentation strictly, or preview it with live reload:

```shell
make docs-build
make docs-serve
```

These commands install the locked `docs` dependency group as needed and regenerate the static
OpenAPI schema from the application. Do not edit `docs/api/openapi.json` by hand or commit it.

## Code and tests

Use British English in prose and identifiers. Prefer clear, direct code and reuse existing helpers
instead of duplicating validation or rendering logic.

Add or update tests before changing behaviour. Test observable outcomes and error classifications,
not implementation details. Changes to inline file-key rules belong in
`tests/test_inline_file_key_properties.py` as properties as well as focused examples.

Keep caller faults as 4xx responses and infrastructure faults as 5xx responses. Never include a
request body, credential, proprietary template or other sensitive input in an error message, log,
fixture or example.

Regenerate the checked-in request example after changing its source files:

```shell
make regenerate-examples
```

Use `uv` to change dependencies and commit the resulting `uv.lock` update.

## Pull requests

A pull request should:

- explain the problem and the chosen solution;
- remain limited to one coherent change;
- include tests for changed behaviour;
- update the documentation and example content when the public contract changes;
- pass `make docs-build` when documentation or the OpenAPI contract changes;
- pass the relevant tests, linting and type checking;
- call out compatibility, deployment or container-image implications;
- avoid committing credentials, customer data or proprietary templates.

Maintainers may ask for changes or decline work that does not fit the project's scope. Review and
acceptance are not guaranteed.

## Commit messages

Prefer Conventional Commit messages, for example:

```text
feat: add SVG page selection
fix: reject colliding inline file keys
docs: clarify font licensing responsibilities
test: cover render timeout classification
ci: pin the container build input
```

Use `!` or a `BREAKING CHANGE:` footer when a commit deliberately changes an existing public
contract.

## Licence

By submitting a contribution, you agree that it may be distributed under the Apache Licence,
Version 2.0.
