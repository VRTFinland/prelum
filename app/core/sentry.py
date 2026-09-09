"""Optional Sentry error tracking.

The SDK is an optional dependency (`pip install prelum[sentry]`, or `uv sync --extra sentry`), so
that a deployment which does not use Sentry does not carry a vendor SDK with network access it never
calls. Everything below degrades to a no-op when the package is absent — except a configured DSN,
which is a misconfiguration and says so loudly rather than dropping errors on the floor.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

import structlog
from structlog.typing import FilteringBoundLogger

from app.core.constants import API_TOKEN_HEADER

if TYPE_CHECKING:
    # The SDK is an optional extra, so its types may only be referenced in annotations, which
    # `from __future__ import annotations` above keeps as strings at runtime.
    from sentry_sdk.types import Event, Hint

try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    SENTRY_AVAILABLE: Final = True
except ImportError:  # pragma: no cover - exercised by test_sentry_optional.py via sys.modules
    SENTRY_AVAILABLE: Final = False

logger: FilteringBoundLogger = structlog.get_logger()

_FILTERED: Final = "[Filtered]"
_TOKEN_HEADER_LOWER: Final = API_TOKEN_HEADER.lower()


def scrub_event(event: Event, _hint: Hint) -> Event:
    """Redact the API token header from an outgoing event.

    `send_default_pii=False` redacts only the header names the SDK ships with — cookie,
    authorization, x-api-key and a few others. A service-specific token header is not among them, so
    without this hook the shared credential travels in `request.headers` of every captured event,
    and every 5xx is captured because errors are logged at ERROR level.
    """
    request = event.get("request")
    if not isinstance(request, dict):
        return event

    headers = request.get("headers")
    if isinstance(headers, dict):
        for name in headers:
            if isinstance(name, str) and name.lower() == _TOKEN_HEADER_LOWER:
                headers[name] = _FILTERED

    return event


def setup_sentry(
    dsn: str | None,
    environment: str | None,
    traces_sample_rate: float | None = None,
) -> None:
    """Initialise Sentry, or leave it uninitialised when no DSN is configured.

    :param dsn: Sentry DSN. None or empty string disables Sentry.
    :param environment: Environment name (e.g., 'production', 'staging').
    :param traces_sample_rate: Optional sample rate for performance tracing (0.0 to 1.0).
    """
    if not dsn:
        return  # Sentry disabled when DSN is empty

    if not SENTRY_AVAILABLE:
        # A DSN was configured, so the operator expects errors to be reported. Starting anyway with
        # reporting silently disabled is the worst outcome: the service looks monitored and is not.
        logger.error(
            "sentry.sdk_missing",
            message=(
                "PRELUM_SENTRY_DSN is set but the sentry-sdk package is not installed. "
                "Install the 'sentry' extra to enable error reporting, or unset the DSN."
            ),
        )
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=environment or "",
        max_breadcrumbs=10,
        traces_sample_rate=traces_sample_rate,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            LoggingIntegration(
                level=logging.WARNING,  # Capture WARNING+ as breadcrumbs
                event_level=logging.ERROR,  # Send ERROR+ as events
            ),
        ],
        send_default_pii=False,
        # Caller source, data and inline files are customer content, and the SDK would otherwise
        # attach the parsed request body up to its default "medium" limit.
        max_request_body_size="never",
        before_send=scrub_event,
        before_send_transaction=scrub_event,
    )
