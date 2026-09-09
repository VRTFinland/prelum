# Pinned by version and multi-platform manifest digest: rendered output must not
# depend on which Typst release an image rebuild happens to resolve.
FROM ghcr.io/typst/typst:0.15.1@sha256:032e292249bcd378480cc7c142cfa324b63ef8aadeb88d7e7230320c4c9c422f AS typst

FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONOPTIMIZE=2 \
    PYTHONNOUSERSITE=1

WORKDIR /prelum

FROM base AS builder

# CI runs integration tests in this stage, so it needs the production Typst binary and fonts.
RUN --mount=type=cache,id=apt-lists,target=/var/lib/apt/lists/,sharing=locked \
    --mount=type=cache,id=apt-packages,target=/var/cache/apt/,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    fonts-liberation \
    fonts-montserrat

COPY --from=typst /bin/typst /usr/local/bin/typst

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

# Keep the environment outside /prelum so the final stage can copy it without project sources.
ENV UV_PROJECT_ENVIRONMENT=/venv \
    UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv,sharing=locked \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    # The deployed image supports the optional Sentry integration.
    uv sync --locked --no-dev --extra sentry

COPY pyproject.toml uv.lock /prelum/
COPY app /prelum/app
COPY examples /prelum/examples
COPY docs /prelum/docs
COPY mkdocs.yml /prelum/mkdocs.yml
COPY .github/workflows /prelum/.github/workflows
COPY Makefile /prelum/Makefile
COPY scripts /prelum/scripts
COPY tests /prelum/tests

FROM base AS final

RUN --mount=type=cache,id=apt-lists,target=/var/lib/apt/lists/,sharing=locked \
    --mount=type=cache,id=apt-packages,target=/var/cache/apt/,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends ca-certificates fonts-dejavu-core \
    fonts-liberation \
    fonts-montserrat \
    && rm -rf /var/lib/apt/lists/*

COPY --from=typst /bin/typst /usr/local/bin/typst
COPY --from=builder /venv /venv
COPY --from=builder /prelum/app /prelum/app

# The upstream Typst image omits its licence files; Apache-2.0 requires them in distributions.
# These copies are pinned to the same revision as the binary.
COPY third-party/typst/LICENSE third-party/typst/NOTICE /usr/share/licenses/typst/
COPY LICENSE /usr/share/licenses/prelum/LICENSE

# Run caller-supplied Typst source as a fixed non-root uid.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin prelum
USER 10001:10001

ENV PRELUM_CLI_PATH=/usr/local/bin/typst
ENV PRELUM_BIND=0.0.0.0
ENV PRELUM_PORT=9870
ENV PATH="/venv/bin:${PATH}"
# Keep the module entry point independent of the container's working directory.
ENV PYTHONPATH=/prelum

EXPOSE 9870

CMD ["python", "-m", "app"]
