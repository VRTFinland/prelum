"""Log records must be machine-readable: one flat JSON object per line, not JSON inside JSON."""

import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from app.core.logging import setup_logging


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """setup_logging mutates global structlog and stdlib state, so put both back afterwards."""
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()


def _emit_and_capture(capsys: pytest.CaptureFixture[str], emit) -> dict[str, object]:
    setup_logging()
    emit()
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected exactly one log line, got {lines}"
    return json.loads(lines[0])


def test_event_fields_are_top_level_not_nested_json(capsys: pytest.CaptureFixture[str]):
    """
    The regression: structlog rendered the event, then ProcessorFormatter rendered it again, so
    `message` held a JSON string and every field was invisible to ELK. Fields must be indexable.
    """
    record = _emit_and_capture(
        capsys,
        lambda: structlog.get_logger().info("render.complete", bytes=9875, output_format="pdf"),
    )

    assert record["message"] == "render.complete"
    assert record["bytes"] == 9875
    assert record["output_format"] == "pdf"
    assert record["level"] == "info"
    assert "@timestamp" in record


def test_message_is_not_itself_json(capsys: pytest.CaptureFixture[str]):
    """Guards the double-encoding directly: a message that parses as JSON means it was rendered twice."""
    record = _emit_and_capture(capsys, lambda: structlog.get_logger().info("render.start", bytes=1))

    with pytest.raises(json.JSONDecodeError):
        _ = json.loads(str(record["message"]))


def test_foreign_stdlib_logs_are_rendered_once(capsys: pytest.CaptureFixture[str]):
    """Third-party loggers (uvicorn, fastapi) go through the same formatter and must not double-encode."""
    record = _emit_and_capture(
        capsys,
        lambda: logging.getLogger("uvicorn.error").warning("Application startup complete"),
    )

    assert record["message"] == "Application startup complete"
    assert record["level"] == "warning"


def test_exception_info_is_rendered_as_a_field(capsys: pytest.CaptureFixture[str]):
    """format_exc_info must survive the formatter change, or error logs lose their tracebacks."""

    def emit() -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            structlog.get_logger().exception("render.failed")

    record = _emit_and_capture(capsys, emit)

    assert record["message"] == "render.failed"
    assert "ValueError: boom" in str(record["exception"])


def test_request_context_is_bound_as_a_field(capsys: pytest.CaptureFixture[str]):
    """The request id is what correlates a request's lines, so it must be a field, not buried text."""
    from app.core.logging import bind_request_context, clear_request_context

    def emit() -> None:
        bind_request_context("req-123")
        try:
            structlog.get_logger().info("render.start")
        finally:
            clear_request_context()

    record = _emit_and_capture(capsys, emit)

    assert record["request_id"] == "req-123"


def test_error_context_is_logged_as_one_nested_field(capsys: pytest.CaptureFixture[str]):
    """
    A problem's `context` is logged whole, under its own key, and never spread across the record.

    Spreading it would collide with the record's own `status` and `title`, silently replacing an
    operational field with a caller-supplied one. Keeping it nested is the deliberate trade: the
    whole `files` key reaches the logging pipeline, bounded by MAX_KEY_LENGTH and already visible
    to the caller that sent it. See "Error privacy" in docs/operations/security-model.md.
    """
    from fastapi import Request

    from app.core.errors import TemplateFileTooLargeError, app_error_handler

    key = "assets/" + "a" * 200 + ".png"
    error = TemplateFileTooLargeError(key=key, size=2, limit=1)
    request = Request({"type": "http", "method": "POST", "path": "/v1/render", "headers": [], "query_string": b""})

    record = _emit_and_capture(capsys, lambda: app_error_handler(request, error))

    assert record["context"] == {"key": key, "size": 2, "limit": 1}
    assert record["status"] == 413
    assert record["title"] == "Template Too Large"
    # The prose stays shortened even though the field beside it is whole.
    assert key not in str(record["detail"])
