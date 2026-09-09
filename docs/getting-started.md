# Getting started

## Requirements

Local development requires Python 3.14 or later, [uv](https://docs.astral.sh/uv/) and the Typst CLI.
The container build already carries the pinned Typst version and default report fonts.

Install dependencies and start a development instance:

```bash
uv sync
PRELUM_ENVIRONMENT=development uv run python -m app
```

The `development`, `local`, `dev` and `test` environments supply the public
`dev-only-insecure-token`. Every other environment must provide `PRELUM_API_TOKEN`; otherwise the
service refuses to start.

## Render a document

```bash
curl -X POST http://localhost:9870/v1/render \
  -H "Content-Type: application/json" \
  -H "X-Prelum-Api-Token: dev-only-insecure-token" \
  --data '{
    "source": "#set page(width: 80mm, height: auto)\n= Hello\nRendered by Prelum.",
    "output": {"format": "pdf", "filename": "hello.pdf"}
  }' \
  --output hello.pdf
```

A successful direct render returns the artefact bytes with `Content-Disposition: inline` and
`Cache-Control: no-store`. See [Errors](api/errors.md) for the problem response contract.

## Run with Docker

```bash
docker build -t prelum:latest .
docker run --rm -p 9870:9870 \
  -e PRELUM_API_TOKEN=replace-me \
  prelum:latest
```

The final image runs as uid 10001. See [Docker and releases](operations/docker.md) for mounted
directories, immutable release digests and signature verification.

## Service endpoints

| Endpoint | Purpose | Authentication |
| --- | --- | --- |
| `POST /v1/render` | Compile a caller-supplied Typst project | `X-Prelum-Api-Token` |
| `GET /health` | Liveness response `{"status":"ok"}` | none |
| `GET /metrics` | Prometheus metrics | none |
| `GET /openapi.json` | Generated OpenAPI schema | none |
| `GET /docs` | Swagger UI | none |
| `GET /redoc` | ReDoc | none |
