# Explore the API with OpenAPI

Prelum publishes an OpenAPI description of its endpoints, request fields and responses. Use the
interactive documentation to try a request by hand, or download the schema when generating or
validating client code.

## Try an endpoint in the browser

A running Prelum instance provides:

- Swagger UI at `/docs`, with forms for sending requests;
- ReDoc at `/redoc`, for browsing the API reference;
- the raw OpenAPI document at `/openapi.json`.

Rendering and constraint requests still require an `X-Prelum-Api-Token` header. The interactive
documentation itself is public.

## Download the contract

[Download the schema generated for this documentation](openapi.json), or fetch
`/openapi.json` from the deployment your client will use. The deployment copy is useful when a
client needs to confirm that it is talking to the expected Prelum version.

The schema describes the stable request shape. Deployment-specific file counts and byte limits
come from authenticated `GET /v1/constraints`; see
[Constraints and files-key rules](files-key-rules.md).

## How it stays current

Prelum generates the schema directly from the FastAPI application rather than maintaining it by
hand. `make docs-build` and `make docs-serve` regenerate the static copy, and the strict
documentation build fails if it cannot consume the result.
