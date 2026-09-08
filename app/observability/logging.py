"""Structured logging with JSON and text formatters.

Call ``setup_logging()`` once at startup to configure the root logger.
Use ``get_logger(__name__)`` in any module to get a named logger.

JSON format (default):
    {"timestamp": "...", "level": "INFO", "logger": "...", "message": "...", "extra": {...}}

Text format (human-readable):
    2026-01-01 12:00:00 [INFO] module: message extra={...}
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Attach any extra fields passed via ``extra={}``.
        for key in ("triage_id", "repository", "workflow_run_id", "step", "duration_ms"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable format for local development."""

    _FMT = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"

    def __init__(self) -> None:
        super().__init__(self._FMT, datefmt="%Y-%m-%d %H:%M:%S")


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure the root logger once at application startup."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates on re-init.
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(TextFormatter())
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger (convenience wrapper)."""
    return logging.getLogger(name)
