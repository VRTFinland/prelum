import logging
import logging.config
import os
import sys

import structlog
from structlog.dev import ConsoleRenderer
from structlog.stdlib import ProcessorFormatter
from structlog.typing import EventDict, WrappedLogger


def rename_event_to_message(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """
    Rename structlog's 'event' field to 'message' and 'timestamp' to '@timestamp'.

    This ensures compatibility with ELK stack and Filebeat, which expect
    logs to have '@timestamp' and 'message' fields for proper indexing.
    """
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    if "timestamp" in event_dict:
        event_dict["@timestamp"] = event_dict.pop("timestamp")
    return event_dict


def setup_logging() -> None:
    """
    Configure logging to route both structlog and stdlib logging through structlog processors.

    Uses ProcessorFormatter to ensure third-party library logs (FastAPI, uvicorn, etc.)
    are formatted consistently with structlog logs and include proper ELK-compatible fields.
    """
    pretty_logs = os.getenv("PRELUM_LOG_PRETTY", "").lower() in {"1", "true", "yes"}
    renderer = ConsoleRenderer() if pretty_logs else structlog.processors.JSONRenderer()

    # Shared processors for both structlog and stdlib logging
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        rename_event_to_message,
    ]

    # Configure stdlib logging to use structlog processors
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structlog": {
                "()": ProcessorFormatter,
                "processor": renderer,
                "foreign_pre_chain": shared_processors,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "structlog",
                "stream": sys.stdout,
            },
        },
        "loggers": {
            "": {
                "handlers": ["console"],
                "level": "DEBUG" if pretty_logs else "INFO",
            },
            # Reduce verbosity of common third-party libraries
            "uvicorn.access": {"level": "WARNING"},
        },
    }

    logging.config.dictConfig(logging_config)

    # Configure structlog. The chain ends at wrap_for_formatter, NOT at the renderer: structlog
    # hands the event dict to the stdlib logger, and ProcessorFormatter above is what renders it.
    # Rendering here as well would encode the event twice, burying every field inside a JSON string
    # in `message` — which is exactly what the ELK compatibility above exists to avoid.
    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def bind_request_context(request_id: str | None = None) -> None:
    if request_id:
        _ = structlog.contextvars.bind_contextvars(request_id=request_id)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()
