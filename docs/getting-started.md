# Getting started

This guide takes you from the source repository to your first rendered PDF. The quickest route is
Docker; running from Python is useful when developing Prelum itself.

## Start Prelum with Docker

Build the image from the repository and start a development instance:

```bash
docker build -t prelum:latest .
docker run --rm -p 9870:9870 \
  -e PRELUM_ENVIRONMENT=development \
  prelum:latest
```

The service now listens at `http://localhost:9870`. Development mode supplies the public token
`dev-only-insecure-token`; never use that token for a deployed service.

## Render your first PDF

Leave the container running and execute this command in another terminal:

```bash
curl --silent --show-error --fail-with-body \
  http://localhost:9870/v1/render \
  -H "Content-Type: application/json" \
  -H "X-Prelum-Api-Token: dev-only-insecure-token" \
  --data '{"source":"#set page(width: 80mm, height: auto)\n= Hello\nRendered by Prelum."}' \
  --output hello.pdf
```

Open `hello.pdf` to see the result. The response body is the PDF itself; it is not wrapped in
JSON. If the request fails, `--fail-with-body` reports the HTTP failure and preserves Prelum's
human-readable error response.

The [render guide](api/render.md) shows how to pass data and additional files. See
[Output formats](api/output-formats.md) when you need PNG, SVG, page selection or a multi-page ZIP.

## Run from the source tree

For local development, install Python 3.14 or later, [uv](https://docs.astral.sh/uv/) and the Typst
CLI. Then run:

```bash
uv sync
PRELUM_ENVIRONMENT=development uv run python -m app
```

This starts the same development service on port 9870, so the render command above works unchanged.
The `development`, `local`, `dev` and `test` environments supply the public development token.
Every other environment must set `PRELUM_API_TOKEN`, or the service refuses to start.

## Before deploying

Use a private API token, place Prelum behind suitable network controls and review the
[security model](operations/security-model.md). The [configuration guide](operations/configuration.md)
explains resource limits, concurrency and optional integrations. For immutable release images,
mounts and signature verification, see [Docker and releases](operations/docker.md).

## Service endpoints

| Endpoint | What it is for | Authentication |
| --- | --- | --- |
| `POST /v1/render` | Render a caller-supplied Typst project | API token |
| `GET /v1/constraints` | Read the published rules and this deployment's limits | API token |
| `GET /health` | Check that the process is alive | none |
| `GET /metrics` | Collect Prometheus metrics | none |
| `GET /openapi.json` | Download the generated API contract | none |
| `GET /docs` | Try the API with Swagger UI | none |
| `GET /redoc` | Browse the API reference with ReDoc | none |

Authenticated requests use the `X-Prelum-Api-Token` header.
