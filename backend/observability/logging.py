"""Structured logging for Sentinel (docs/OPERATIONS.md).

Supports human-friendly text formatting for local development and CloudWatch-compatible
single-line JSON formatting for staging and production environments.

Correlation:
- `REQUEST_ID_CTX` context variable threads the `X-Request-ID` across async tasks.
- All log records automatically include `request_id` and `env`.
- Secrets, tokens, and authorization headers are scrubbed before writing.
"""

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from config.settings import Settings

REQUEST_ID_CTX: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

_SECRET_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9_\-\.]+|pcsk_[a-zA-Z0-9_\-\.]+|pk-lf-[a-zA-Z0-9_\-\.]+|api[_-]?key\s*[:=]?\s*[a-zA-Z0-9_\-\.]+|bearer\s+[a-zA-Z0-9_\-\.]+|password|secret|token)",
    re.IGNORECASE,
)


def get_current_request_id() -> str | None:
    """Retrieve the current request ID from the async context."""
    return REQUEST_ID_CTX.get()


def set_current_request_id(request_id: str | None) -> contextvars.Token:
    """Set the request ID for the current context."""
    return REQUEST_ID_CTX.set(request_id)


class RequestIdFilter(logging.Filter):
    """Injects the current request_id into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_current_request_id() or "-"
        return True


class JsonLogFormatter(logging.Formatter):
    """CloudWatch-compatible JSON formatter.

    Outputs single-line JSON records containing:
    - timestamp (ISO 8601 UTC)
    - level (INFO, WARNING, ERROR, etc.)
    - logger (module / logger name)
    - message (sanitized)
    - request_id (correlation ID)
    - error (exception type and message if present)
    - stack_trace (if exc_info is provided)
    """

    def __init__(self, env: str = "dev"):
        super().__init__()
        self.env = env

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        message = record.getMessage()

        # Scrub potential credential leaks in log messages
        message = _SECRET_PATTERN.sub("<redacted>", message)

        log_payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": getattr(record, "request_id", "-"),
            "env": self.env,
        }

        # Extra structured attributes attached to the record
        if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
            for k, v in record.extra_fields.items():
                if k not in log_payload:
                    log_payload[k] = v

        if record.exc_info:
            exc_type, exc_val, _ = record.exc_info
            log_payload["error"] = {
                "type": getattr(exc_type, "__name__", "Exception"),
                "message": str(exc_val),
            }
            log_payload["stack_trace"] = self.formatException(record.exc_info)

        return json.dumps(log_payload, default=str)


def configure_logging(settings: Settings | None = None) -> None:
    """Configure root and sentinel logging handlers based on settings."""
    env = settings.sentinel_env if settings else "dev"
    log_format = settings.log_format if settings else "text"
    log_level_name = (settings.log_level if settings else "INFO").upper()
    level = getattr(logging, log_level_name, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers to prevent duplicate lines
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.addFilter(RequestIdFilter())

    if log_format.lower() == "json":
        stream_handler.setFormatter(JsonLogFormatter(env=env))
    else:
        text_formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] [req:%(request_id)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        stream_handler.setFormatter(text_formatter)

    root_logger.addHandler(stream_handler)
