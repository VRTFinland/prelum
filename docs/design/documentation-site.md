# Documentation Site Design

**Date:** 2026-08-31
**Status:** Implemented
**Scope:** Prelum documentation and GitHub Pages publication

## Background

Prelum maintains its detailed public contract in a searchable MkDocs site and keeps `README.md` as
the concise repository entry point. Implemented and proposed records live under `docs/design` with
an explicit status. FastAPI also exposes a runtime OpenAPI document at `/openapi.json`, Swagger UI
at `/docs` and ReDoc at `/redoc`.

The repository already uses Markdown, uv and GitHub Actions. Releases remain deliberately manual:
the release workflow validates a version, creates an immutable tag, delegates the tested image
publication, and creates the GitHub release only after publication succeeds. Documentation must fit
that model without coupling a Pages update to a product release.

## Goals

- Publish a searchable project and documentation site at `https://vrtfinland.github.io/prelum/`.
- Build the same documentation locally, in pull requests and for Pages through one command.
- Make detailed public documentation authoritative in `docs/` and keep `README.md` concise.
- Generate OpenAPI from the running application model rather than maintain a second schema.
- Represent `X-Prelum-Api-Token` accurately as required OpenAPI authentication.
- Build documentation strictly in every pull request and publish only reviewed `main` content.
- Preserve the manual container and GitHub release process unchanged.
- Keep implemented and proposed design records distinguishable without publishing them on the site.

## Non-goals

- A custom domain, analytics, a blog or a comment system.
- Versioned documentation or parallel documentation for multiple releases.
- Publishing documentation from release tags.
- Running Prelum merely to generate documentation.
- A second hand-written API schema or duplicated configuration reference.
- Presenting proposed output delivery as an implemented capability.

## Source ownership

Each fact has one authoritative home:

| Content | Authority | Other surfaces |
| --- | --- | --- |
| Overview and first command | `README.md` | Documentation home links back to the repository |
| HTTP request, response and errors | Generated OpenAPI plus `docs/api/` explanations | README links to the site |
| Operator configuration | `docs/operations/configuration.md` | README contains only the quick-start variables |
| Contribution process | Root `CONTRIBUTING.md` | Site navigation links to the GitHub file |
| Engineering invariants | `AGENTS.md` | Public docs describe effects, not internal mechanisms |
| Design decisions | `docs/design/` | Excluded from the public site |

README must remain useful when read from a package index or cloned repository, but it must not
repeat the full option, error-code or environment-variable tables after the site owns them.

## Site structure

```text
docs/
├── index.md
├── getting-started.md
├── api/
│   ├── render.md
│   ├── output-formats.md
│   ├── errors.md
│   └── openapi.md
├── operations/
│   ├── configuration.md
│   ├── docker.md
│   ├── fonts-and-packages.md
│   └── security-model.md
└── design/
    ├── render-api-v1.md
    ├── multi-page-image-output.md
    ├── output-delivery.md
    └── documentation-site.md
```

`mkdocs.yml` defines the public navigation explicitly and excludes `docs/design/` from the build.
The root `CONTRIBUTING.md`, licence, issue tracker and repository are external navigation links
rather than copied Markdown files.

The output-delivery record remains an internal proposal. It must not be published or linked from
feature or API documentation as if the contract existed.

## Tooling and one build path

MkDocs Material belongs in a `docs` dependency group in `pyproject.toml`; uv locks it with every
other development input. It is not a runtime dependency and must not enter the final image.

The Makefile owns the user- and CI-facing commands:

- `make docs-build` generates OpenAPI and runs `mkdocs build --strict`;
- `make docs-serve` generates OpenAPI and runs the local live-reload server.

The Pages workflow calls `make docs-build`; it must not reproduce those commands. Generated site
files live under `site/` and generated `docs/api/openapi.json` is ignored by Git. Neither is source.

## Generated OpenAPI

`scripts/export_openapi.py` imports `create_app`, serialises `app.openapi()` deterministically and
writes `docs/api/openapi.json`. It neither starts Uvicorn nor makes a network request. The Make
targets set `PRELUM_ENVIRONMENT=test`, because Settings deliberately refuses to construct without
a production token outside a development environment.

The schema must contain `/v1/render`, `/health` and their current models. `/v1/render` uses a FastAPI
`APIKeyHeader` security dependency named `X-Prelum-Api-Token`, with automatic rejection disabled so
the existing constant-time comparison and published `forbidden` response remain the only runtime
authentication path. OpenAPI then declares the header as required authentication instead of an
optional ordinary parameter. This refactor must not change status codes or response bodies.

Tests call the exporter with a temporary destination and assert:

- valid JSON is written without starting a server;
- `/v1/render` and `/health` exist;
- the API-key security scheme uses the header and correct name;
- the render operation requires that scheme;
- the render request schema still requires `source`.

## GitHub Pages workflow

`.github/workflows/docs.yml` runs on pull requests, `main` pushes and manual dispatches. It has two
jobs:

1. `build` checks out the repository, installs uv, syncs the locked docs group and runs
   `make docs-build`. On a publishable public `main` event it also uploads `site/` as the Pages
   artifact; pull requests and private repositories only perform the strict build.
2. `deploy` depends on `build`, runs only for a public repository on a `main` push or manual
   dispatch from `main`, and deploys the artifact to the `github-pages` environment.

The workflow defaults to `contents: read`. Only `deploy` receives `pages: write` and
`id-token: write`. Pull requests prove that the site builds but cannot publish it. Concurrency is
scoped to Pages and superseded deployments are cancelled.

Documentation publication after a reviewed merge is not a Prelum release. `.github/workflows/release.yml`,
image tags, Cosign signing, SBOM and provenance remain untouched.

Workflow tests pin the security-sensitive shape: PRs do not deploy, deployment requires the public
repository and `main`, and write permissions exist only on the deploy job.

## Documentation contract

The initial site documents:

- a Docker quick start and a complete render request;
- the required API token and development token behaviour;
- PDF, direct PNG/SVG and multi-page ZIP output;
- page selection, PPI, file and output limits;
- request-provided and mounted fonts plus local packages;
- network isolation and the `.wasm` speed-bump limitation;
- all settings and published problem codes;
- manual releases, immutable image digests, Cosign, SBOM and provenance;
- contribution links.

The docs state that `/openapi.json`, `/docs` and `/redoc` are also available from a running Prelum
instance. The generated schema on Pages is a static reference for readers without a deployment.

## Test-first delivery

1. Add failing exporter and OpenAPI authentication tests.
2. Refactor authentication to `APIKeyHeader` without changing runtime responses.
3. Implement the exporter until its tests pass.
4. Add MkDocs configuration and content, then make strict local builds pass.
5. Add failing workflow-shape tests and implement the Pages workflow.
6. Reduce README duplication only after the site contains the moved information.
7. Run the full application suite, lint, type checking, formatting, strict docs build and
   `git diff --check`.

## Consequences

- Documentation changes become reviewable build inputs rather than unchecked Markdown.
- `main` always describes the site that Pages publishes, while releases remain manually initiated.
- API model changes automatically reach the static OpenAPI artefact on the next docs build.
- The site adds development dependencies and CI time but no runtime dependency or service.
- Documentation versioning can be designed later if more than one supported public API requires it.
