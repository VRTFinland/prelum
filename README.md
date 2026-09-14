# Prelum

FastAPI service that compiles caller-supplied [Typst](https://typst.app) projects to PDF, SVG or
PNG. Prelum owns no template registry: each request contains its source, auxiliary files and data.

[Read the documentation](https://vrtfinland.github.io/prelum/) for the complete API, output-format
and deployment reference.

## Features

- one strict, versioned `POST /v1/render` API;
- PDF versions, PDF/A and PDF/UA conformance options;
- direct PNG and SVG output or bounded multi-page ZIP archives;
- request-local assets and fonts;
- read-only operator-mounted fonts and local Typst packages;
- bounded request, queue, compiler and output resources;
- a non-root multi-platform image with SBOM, provenance and a Cosign signature.

## Quick start

Build and start a development instance with the published development token:

```bash
docker build -t prelum:latest .
docker run --rm -p 9870:9870 \
  -e PRELUM_ENVIRONMENT=development \
  prelum:latest
```

Render the checked-in example:

```bash
curl -X POST http://localhost:9870/v1/render \
  -H "Content-Type: application/json" \
  -H "X-Prelum-Api-Token: dev-only-insecure-token" \
  -d @examples/render-request.json \
  --output hello.pdf
```

The `development`, `local`, `dev` and `test` environments supply that public token. Every other
environment must set `PRELUM_API_TOKEN` or the service refuses to start.

See the documentation for the [render request](docs/api/render.md),
[output formats](docs/api/output-formats.md), [configuration](docs/operations/configuration.md),
[fonts and local packages](docs/operations/fonts-and-packages.md), and
[security model](docs/operations/security-model.md).

> [!WARNING]
> A render request contains Typst source that reaches the compiler. Authentication controls who
> may submit source; it does not make that source trusted.

## Development

Prelum requires Python 3.14 or later, [uv](https://docs.astral.sh/uv/) and the Typst CLI. Install
dependencies and start the service with:

```bash
uv sync
PRELUM_ENVIRONMENT=development uv run python -m app
```

Run the available checks:

```bash
# Unit tests; no Typst CLI required
uv run pytest -m "not integration"

# Integration tests or the complete suite; Typst must be on PATH
uv run pytest -m integration
uv run pytest

# Lint and type checks
uv run ruff check .
uv run ty check app
```

Equivalent Make targets are `make test`, `make test-integration`, `make test-all`, `make lint`,
`make type-check` and `make checks`. Integration tests never self-skip: CI runs them with the real
Typst binary and default report fonts.

Build or preview the documentation with:

```bash
make docs-build
make docs-serve
```

Both commands regenerate the static OpenAPI schema directly from the application. A running
Prelum instance also exposes `/openapi.json`, Swagger UI at `/docs` and ReDoc at `/redoc`.

See [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change.

## Releases

Published images use `ghcr.io/vrtfinland/prelum:<tag>`. Releases are started manually from the
`release` workflow on `main`; image publication, SBOM, provenance, Cosign signing and the GitHub
release all run only after the complete test job succeeds. See
[Docker and releases](docs/operations/docker.md) for immutable-digest verification and retry
behaviour.

## Licence

Licensed under the Apache Licence, Version 2.0. See [LICENSE](LICENSE).
