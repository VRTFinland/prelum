# Image tag. A tagged release builds as the tag itself (v1.2.3 -> 1.2.3, with -N-gsha
# appended for commits after it); an untagged tree falls back to the pyproject version
# plus the commit, e.g. 1.0.0-ge6d36ba. Either form gains -dirty for uncommitted changes.
git_describe := $(shell git describe --tags --dirty 2>/dev/null | sed -e 's/^v//')
package_version := $(shell sed -n 's/^version = "\([^"]*\)".*/\1/p' pyproject.toml | head -1)
commit := $(shell git rev-parse --short HEAD 2>/dev/null)
dirty := $(shell git diff --quiet HEAD 2>/dev/null || echo -dirty)
version := $(or ${git_describe},$(if ${package_version},${package_version}$(if ${commit},-g${commit})${dirty}),dev)
DOCKER_TAG ?= ${version}
# Override REGISTRY to push somewhere other than the local daemon, e.g.
#   make push REGISTRY=ghcr.io/vrtfinland
IMAGE_NAME ?= prelum
REGISTRY ?=
IMAGE := $(if ${REGISTRY},${REGISTRY}/,)${IMAGE_NAME}:${DOCKER_TAG}
LATEST_IMAGE := $(if ${REGISTRY},${REGISTRY}/,)${IMAGE_NAME}:latest
DOCS_PORT ?= 9876

.PHONY: version build buildx-build-publish push run serve test test-all test-integration test-docker lint lint-fix format type-check checks regenerate-examples docs-examples docs-openapi docs-build docs-serve

# Print the tag the next build would use, e.g. for CI or `docker run`.
version:
	@echo ${DOCKER_TAG}

build:
	@docker build --progress plain --load -t ${IMAGE} -t ${LATEST_IMAGE} .

buildx-build-publish:
	@docker buildx build \
		--platform=linux/amd64,linux/arm64 \
		--progress plain \
		--push \
		-t ${IMAGE} .

push:
	@docker push ${IMAGE}

run:
	@docker run -it --rm -p 9870:9870 -e PRELUM_ENVIRONMENT=development ${IMAGE}

# Runs from the source tree against whatever typst is on PATH, so it is a development convenience
# rather than a supported deployment; the container pins the compiler by digest. The bind address
# is narrowed because the development environment carries a published token.
serve:
	@PRELUM_ENVIRONMENT=development PRELUM_BIND=127.0.0.1 uv run python -m app

# Test commands
test:
	@echo "Running unit tests (no typst CLI required)..."
	@uv run pytest -m "not integration"

test-all:
	@echo "Running full test suite including integration tests..."
	@uv run pytest

test-integration:
	@echo "Running integration tests (requires typst CLI)..."
	@uv run pytest -m integration

test-docker:
	@echo "Building Docker image with tests..."
	@docker build --progress plain --target builder -t prelum-py-test .
	@echo "Running tests in Docker container..."
	@docker run --rm prelum-py-test sh -c "uv sync --locked --extra sentry && uv run pytest"

# Linting and formatting
lint:
	@echo "Running ruff linter..."
	@uv run ruff check .

lint-fix:
	@echo "Running ruff linter with auto-fix..."
	@uv run ruff check --fix .

format:
	@echo "Running ruff formatter..."
	@uv run ruff format .

# Type checking
type-check:
	@echo "Running ty type checker..."
	@uv run ty check app scripts

# Run all checks
checks: lint type-check
	@echo "All checks passed!"

# Regenerate the example request body from examples/hello.typ and examples/lib/label.typ
regenerate-examples:
	@echo "Regenerating examples/render-request.json from the .typ files..."
	@uv run python scripts/regenerate-example-request.py

# Render the published example artefacts. The manual is built from the documentation itself, so
# it can only be correct when it is produced by the same build that produces the site.
docs-examples:
	@echo "Rendering docs/examples from examples/<name>/..."
	@PRELUM_ENVIRONMENT=test uv run --group docs python -m scripts.render_examples

docs-openapi:
	@PRELUM_ENVIRONMENT=test uv run python -m scripts.export_openapi

docs-build: docs-examples docs-openapi
	@uv run --group docs mkdocs build --strict

docs-serve: docs-examples docs-openapi
	@uv run --group docs mkdocs serve -a 127.0.0.1:${DOCS_PORT}
