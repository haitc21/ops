"""Structured logging configuration with secret redaction."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

from ops.observability.redaction import redact_mapping, redact_text


class ServiceNameFilter(logging.Filter):
    """Attach a stable service name to every log record."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.__dict__["service"] = self._service_name
        return True


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "service": getattr(record, "service", "ops"),
        }
        for attr in ("correlation_id", "operation_id", "message_id"):
            value = getattr(record, attr, None)
            if value is not None:
                payload[attr] = value
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "message",
                "taskName",
                "service",
                "correlation_id",
                "operation_id",
                "message_id",
            }:
                continue
            payload[key] = redact_mapping(value)
        if record.exc_info:
            payload["exc_info"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(
    level: str = "INFO",
    service_name: str = "ops",
    stream: TextIO | None = None,
) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(RedactingJsonFormatter())
    handler.addFilter(ServiceNameFilter(service_name))
    root.addHandler(handler)
    root.setLevel(level.upper())
