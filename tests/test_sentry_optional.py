"""Sentry is an optional extra, so the path where the SDK is absent must be exercised, not assumed.

Without these tests the no-SDK path is code nobody runs until an outside user clones the repository,
installs without the extra, and gets an ImportError at startup.
"""

import importlib
import sys
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
import structlog

import app.core.sentry as sentry_module
from app.core.constants import API_TOKEN_HEADER


@pytest.fixture
def sentry_absent() -> Iterator[object]:
    """Reimport app.core.sentry with `import sentry_sdk` failing, then put the real module back.

    Setting a sys.modules entry to None makes the import machinery raise ImportError for it, which
    is what an installation without the `sentry` extra looks like.
    """
    saved = {name: module for name, module in sys.modules.items() if name.startswith("sentry_sdk")}
    for name in saved:
        del sys.modules[name]
    sys.modules["sentry_sdk"] = None
    try:
        yield importlib.reload(sentry_module)
    finally:
        del sys.modules["sentry_sdk"]
        sys.modules.update(saved)
        _ = importlib.reload(sentry_module)


def test_module_imports_without_the_sdk(sentry_absent):
    """The import itself must not explode — this is what breaks a default install."""
    assert sentry_absent.SENTRY_AVAILABLE is False


def test_no_dsn_and_no_sdk_is_a_silent_no_op(sentry_absent):
    """Nothing is configured and nothing is expected, so nothing should be said."""
    with structlog.testing.capture_logs() as logs:
        sentry_absent.setup_sentry(dsn=None, environment="production")

    assert logs == []


def test_a_configured_dsn_without_the_sdk_is_reported_loudly(sentry_absent):
    """The operator asked for error reporting and is not getting it; silence would be the worst outcome."""
    with structlog.testing.capture_logs() as logs:
        sentry_absent.setup_sentry(dsn="https://key@example.invalid/1", environment="production")

    assert len(logs) == 1
    assert logs[0]["log_level"] == "error"
    assert logs[0]["event"] == "sentry.sdk_missing"


def test_a_configured_dsn_without_the_sdk_does_not_prevent_startup(sentry_absent):
    """A missing optional dependency must degrade the service, not stop it."""
    sentry_absent.setup_sentry(dsn="https://key@example.invalid/1", environment="production")


def test_the_sdk_is_initialised_when_present(monkeypatch: pytest.MonkeyPatch):
    """The happy path still wires the DSN, environment and integrations through to the SDK."""
    assert sentry_module.SENTRY_AVAILABLE is True
    init = MagicMock()
    monkeypatch.setattr(sentry_module.sentry_sdk, "init", init)

    sentry_module.setup_sentry(dsn="https://key@example.invalid/1", environment="staging", traces_sample_rate=0.25)

    init.assert_called_once()
    kwargs = init.call_args.kwargs
    assert kwargs["dsn"] == "https://key@example.invalid/1"
    assert kwargs["environment"] == "staging"
    assert kwargs["traces_sample_rate"] == 0.25
    assert kwargs["send_default_pii"] is False
    assert len(kwargs["integrations"]) == 2


def test_no_dsn_does_not_initialise_the_sdk(monkeypatch: pytest.MonkeyPatch):
    init = MagicMock()
    monkeypatch.setattr(sentry_module.sentry_sdk, "init", init)

    sentry_module.setup_sentry(dsn=None, environment="production")

    init.assert_not_called()


def test_the_scrubbed_header_is_the_one_the_security_scheme_declares():
    """The scrubber and the security scheme must not drift apart.

    If they ever name different headers the token still authenticates and the scrubber still runs,
    so nothing fails — the credential just starts reaching the error tracker in the clear.
    """
    from app.api.routes import api_token_header

    assert api_token_header.model.name == API_TOKEN_HEADER


@pytest.mark.parametrize("header", ["X-Prelum-Api-Token", "x-prelum-api-token", "X-PRELUM-API-TOKEN"])
def test_the_token_header_is_redacted_whatever_its_case(header: str):
    """ASGI servers lowercase header names, but the casing the SDK hands to before_send is not guaranteed."""
    event: dict[str, Any] = {"request": {"headers": {header: "the-real-token"}}}

    scrubbed = sentry_module.scrub_event(event, {})

    assert scrubbed["request"]["headers"][header] == "[Filtered]"
    assert "the-real-token" not in str(scrubbed)


def test_other_headers_survive_scrubbing():
    """Over-broad redaction would strip the context that makes an event worth reporting."""
    event: dict[str, Any] = {"request": {"headers": {"content-type": "application/json", "user-agent": "curl/8"}}}

    scrubbed = sentry_module.scrub_event(event, {})

    assert scrubbed["request"]["headers"] == {"content-type": "application/json", "user-agent": "curl/8"}


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"request": None},
        {"request": {}},
        {"request": {"headers": None}},
        {"request": "not-a-mapping"},
    ],
)
def test_scrubbing_tolerates_events_without_request_headers(event: dict[str, Any]):
    """before_send runs on every event, including ones the logging integration builds with no request."""
    assert sentry_module.scrub_event(event, {}) is event


def test_the_sdk_is_told_to_scrub_and_to_drop_request_bodies(monkeypatch: pytest.MonkeyPatch):
    """The hooks are inert unless they are actually handed to init()."""
    init = MagicMock()
    monkeypatch.setattr(sentry_module.sentry_sdk, "init", init)

    sentry_module.setup_sentry(dsn="https://key@example.invalid/1", environment="production")

    kwargs = init.call_args.kwargs
    assert kwargs["before_send"] is sentry_module.scrub_event
    assert kwargs["before_send_transaction"] is sentry_module.scrub_event
    assert kwargs["max_request_body_size"] == "never"
