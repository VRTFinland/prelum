import os
import tempfile
from functools import cache
from pathlib import Path
from typing import ClassVar, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# app/core/constraints.py already depends on the same module: the published bounds live beside the
# validation that enforces them, and configuration has to be checked against them.
from app.core import constants
from app.render.templates import DEFAULT_RENDER_TEMP_ROOT, RENDER_TEMP_PREFIX, ensure_temp_root_fits

# The only environments where a built-in token is acceptable. Anything else — including an
# unset or empty value — is treated as production, so a deployment that configures nothing
# refuses to start rather than accepting a token published in this repository.
_DEV_ENVIRONMENTS = {"development", "local", "dev", "test"}

# Measured against typst 0.15.1: a 300-section A4 document aborts under RLIMIT_DATA at 64 MiB and
# renders at 128 MiB. Below this floor a legitimate render dies exactly as a runaway one does, so
# the operator would see a stream of 422s with nothing naming the limit as their cause.
_MIN_RENDER_MEMORY_BYTES = 128 * 1024 * 1024

# Not a secret: it is published here, and Settings refuses it outside _DEV_ENVIRONMENTS.
DEV_DEFAULT_TOKEN = "dev-only-insecure-token"  # noqa: S105


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="PRELUM_",
        case_sensitive=False,
    )

    bind: str = Field(default="0.0.0.0", description="Bind address for the HTTP server")  # noqa: S104
    port: int = Field(default=9870, ge=1, le=65535, description="Port for the HTTP server")
    cli_path: Path = Field(default=Path("typst"), description="Path to typst CLI binary")
    render_timeout_secs: int = Field(default=15, ge=1, description="Render timeout in seconds")
    max_render_memory_bytes: int | None = Field(
        default=None,
        ge=_MIN_RENDER_MEMORY_BYTES,
        description=(
            "RLIMIT_DATA applied to each Typst process, in bytes. Unset disables the bound, which "
            "is the default because the wrapper that applies it exists only on Linux. Worst-case "
            "service memory is this multiplied by max_concurrent_renders."
        ),
    )
    debug_output_dir: Path | None = Field(default=None, description="Directory for saving debug copies")
    temp_root: Path = Field(
        default=DEFAULT_RENDER_TEMP_ROOT,
        # The published image sets no PRELUM_TEMP_ROOT, so the default is the deployment most in
        # need of checking; pydantic would otherwise skip its validator entirely.
        validate_default=True,
        description=(
            "Directory renders create their temporary project in. Must be writable, and short "
            "enough to leave the published files-key length bound intact."
        ),
    )
    font_path: Path | None = Field(default=None, description="Read-only directory recursively searched for fonts")
    local_package_path: Path | None = Field(default=None, description="Read-only directory containing local packages")
    max_request_body_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, description="Max JSON body size")
    # Two rather than a larger number because this multiplies almost every other memory cost: each
    # concurrent render holds a Typst process bounded by max_render_memory_bytes, plus a request
    # body and a finished output buffer on this side, which no rlimit covers. Typst is CPU-bound,
    # so a small pod gains little from more; scale with replicas instead.
    max_concurrent_renders: int = Field(default=2, ge=1, description="Max simultaneous renders")
    max_queue_wait_secs: float = Field(
        default=10.0,
        gt=0,
        description=(
            "How long a request may wait for a render slot before it is shed with 429. Bounds the "
            "caller's worst case; without it an overloaded service queues without limit."
        ),
    )
    retry_after_secs: int = Field(
        default=5,
        ge=1,
        description="Floor for the Retry-After header on a shed request; the value sent is jittered upwards",
    )
    max_string_bytes: int = Field(
        default=1024 * 1024,
        ge=1024,
        description="Max individual string size in JSON payload",
    )
    max_template_source_bytes: int = Field(
        default=512 * 1024,
        ge=1024,
        description="Max inline template source size in bytes",
    )
    max_inline_file_bytes: int = Field(
        default=1024 * 1024,
        ge=1024,
        description="Max size in bytes of a single files entry, after base64 decoding",
    )
    max_inline_files: int = Field(
        default=64,
        ge=1,
        description="Max number of files entries per request",
    )
    max_output_files: int = Field(
        default=64,
        ge=1,
        description="Max number of page files in an image archive",
    )
    max_output_bytes: int = Field(
        default=50 * 1024 * 1024,
        ge=1024,
        description="Max rendered output file size in bytes",
    )
    allow_typst_network: bool = Field(
        default=False,
        description="Allow the typst CLI to reach the network (needed only for @preview packages)",
    )
    sentry_dsn: str | None = Field(default=None, description="Sentry DSN")
    sentry_environment: str | None = Field(default=None, description="Sentry environment name")
    environment: str | None = Field(
        default=None,
        description=(
            "Deployment environment. Only 'development', 'local', 'dev' or 'test' relax the "
            "API-token requirement; anything else, including unset, requires PRELUM_API_TOKEN. "
            "Falls back to PRELUM_SENTRY_ENVIRONMENT when unset."
        ),
    )
    sentry_traces_sample_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Sentry traces sample rate between 0 and 1",
    )
    api_token: str | None = Field(
        default=None,
        description=(
            "Shared API token for X-Prelum-Api-Token, required on every authenticated endpoint. "
            "Auto-set in dev environments."
        ),
    )

    @field_validator("bind")
    @classmethod
    def validate_bind(cls, value: str) -> str:
        """
        Reject a host uvicorn could not bind, without being stricter than uvicorn itself.

        uvicorn accepts hostnames as well as literal addresses, so this checks only for the
        shapes that are certainly unusable: empty, or containing whitespace.
        """
        if not value.strip() or any(character.isspace() for character in value):
            msg = "bind must be a non-empty host without whitespace"
            raise ValueError(msg)
        return value

    @field_validator("font_path", "local_package_path")
    @classmethod
    def validate_resource_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if not value.is_absolute():
            raise ValueError("resource path must be absolute")
        if not value.is_dir():
            raise ValueError("resource path must be an existing directory")
        try:
            _ = next(value.iterdir(), None)
        except OSError as exc:
            raise ValueError("resource path must be readable") from exc
        return value

    @field_validator("temp_root")
    @classmethod
    def validate_temp_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("temp root must be absolute")
        if not value.is_dir():
            raise ValueError("temp root must be an existing directory")
        try:
            ensure_temp_root_fits(value)
        except RuntimeError as exc:
            # Rejected here rather than per render: the key-length bound this protects is published,
            # so a root that voids it is a misconfiguration, not a request-time failure.
            raise ValueError(f"temp root is too long: {exc}") from exc
        try:
            # Proved by writing rather than by os.access, which reports a permission bit and not a
            # read-only mount. A hardened container with readOnlyRootFilesystem and no volume here
            # would otherwise pass every check, answer /health, join its Service and then fail every
            # render with EROFS as a 503.
            with tempfile.TemporaryDirectory(prefix=RENDER_TEMP_PREFIX, dir=value):
                pass
        except OSError as exc:
            raise ValueError(f"temp root must be writable: {exc}") from exc
        return value

    @field_validator("max_render_memory_bytes", mode="before")
    @classmethod
    def read_an_empty_memory_limit_as_unset(cls, value: object) -> object:
        # The image bakes this into ENV, and neither Docker nor Kubernetes can unset an image ENV —
        # only override it. Both the documentation and the validator below tell operators to leave
        # it unset where prlimit cannot work, so an empty value has to be how they say that.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("max_render_memory_bytes")
    @classmethod
    def validate_render_memory_limit(cls, value: int | None) -> int | None:
        if value is None:
            return None
        # Every way the wrapper itself fails — missing, denied by seccomp, given an argument getopt
        # will not attach — exits with a positive status, which the renderer reads as the caller's
        # template failing to compile. A misconfigured limit would therefore answer 422 on every
        # request rather than failing the deployment, so its absence has to stop construction here.
        if not constants.PRLIMIT_PATH.is_file() or not os.access(constants.PRLIMIT_PATH, os.X_OK):
            raise ValueError(
                f"a render memory limit needs the prlimit wrapper at {constants.PRLIMIT_PATH}, which is "
                "Linux-only; leave PRELUM_MAX_RENDER_MEMORY_BYTES unset elsewhere"
            )
        return value

    @field_validator("font_path")
    @classmethod
    def validate_font_path_separator(cls, value: Path | None) -> Path | None:
        if value is not None and os.pathsep in str(value):
            raise ValueError("font path must not contain the path separator")
        return value

    @property
    def effective_environment(self) -> str:
        """The environment the token rules apply to, with the legacy Sentry variable as fallback."""
        return (self.environment or self.sentry_environment or "").lower()

    @property
    def is_development(self) -> bool:
        """Whether this is a dev environment, which is what relaxes the API-token requirement."""
        return self.effective_environment in _DEV_ENVIRONMENTS

    @model_validator(mode="after")
    def enforce_api_token(self) -> Self:
        """
        Supply a token in dev environments, and refuse to start without one anywhere else.

        A token that is empty once stripped of whitespace (e.g. "   ") is treated as absent, not
        as a configured credential: it fails closed the same as an unset value rather than being
        silently trimmed and accepted, since a token of blanks is a misconfiguration to surface,
        not a value to repair on the operator's behalf.
        """
        if self.api_token and self.api_token.strip():
            return self

        if self.is_development:
            object.__setattr__(self, "api_token", DEV_DEFAULT_TOKEN)
            return self

        msg = (
            "PRELUM_API_TOKEN is required unless PRELUM_ENVIRONMENT names a development "
            f"environment ({', '.join(sorted(_DEV_ENVIRONMENTS))}); "
            f"current environment: {self.effective_environment or '<unset>'}"
        )
        raise ValueError(msg)


@cache
def get_settings() -> Settings:
    return Settings()
