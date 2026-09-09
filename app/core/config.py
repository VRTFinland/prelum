import os
from functools import cache
from pathlib import Path
from typing import ClassVar, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The only environments where a built-in token is acceptable. Anything else — including an
# unset or empty value — is treated as production, so a deployment that configures nothing
# refuses to start rather than accepting a token published in this repository.
_DEV_ENVIRONMENTS = {"development", "local", "dev", "test"}

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
    debug_output_dir: Path | None = Field(default=None, description="Directory for saving debug copies")
    font_path: Path | None = Field(default=None, description="Read-only directory recursively searched for fonts")
    local_package_path: Path | None = Field(default=None, description="Read-only directory containing local packages")
    max_request_body_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, description="Max JSON body size")
    max_concurrent_renders: int = Field(default=10, ge=1, description="Max simultaneous renders")
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
