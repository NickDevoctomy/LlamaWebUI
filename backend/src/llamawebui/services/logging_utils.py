"""Structured logging with application-level secret redaction."""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from datetime import UTC, datetime
from typing import Any

_REDACTED = "[REDACTED]"
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)\b(?:hf|lwui)_[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(\b(?:HF_TOKEN|LLAMAWEBUI_HF_TOKEN|API_KEY|TOKEN)\s*[=:]\s*)[^\s,;]+"),
)
_LOG_CAPACITY = 200
_recent_logs: deque[str] = deque(maxlen=_LOG_CAPACITY)


class RedactionFilter(logging.Filter):
    """Redact known secret formats and explicitly registered secret values."""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage(), self._secrets)
        record.args = ()
        for key, value in list(record.__dict__.items()):
            if key in _RESERVED_RECORD_FIELDS:
                continue
            if isinstance(value, str):
                record.__dict__[key] = redact_text(value, self._secrets)
        return True


def redact_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    """Return text with bearer tokens, known token formats, and supplied secrets removed."""
    redacted = value
    for secret in secrets:
        redacted = redacted.replace(secret, _REDACTED)
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(
            lambda match: match.group(1) + _REDACTED if match.lastindex else _REDACTED,
            redacted,
        )
    return redacted


class JsonFormatter(logging.Formatter):
    """Render records as compact JSON with an ISO-8601 UTC timestamp."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str, sort_keys=True)


class RecentLogHandler(logging.Handler):
    """Retain a bounded, already-formatted application log tail."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _recent_logs.append(self.format(record))
        except Exception:
            self.handleError(record)


_RESERVED_RECORD_FIELDS = frozenset(
    logging.LogRecord("llamawebui", 0, "", 0, "", (), None).__dict__
)


def configure_logging(level: str = "INFO", *, secrets: tuple[str, ...] = ()) -> None:
    """Configure the application root logger without exposing secret values."""
    root = logging.getLogger("llamawebui")
    root.setLevel(level.upper())
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    redactor = RedactionFilter(secrets)
    handler.addFilter(redactor)
    recent = RecentLogHandler()
    recent.setFormatter(JsonFormatter())
    recent.addFilter(RedactionFilter(secrets))
    root.addHandler(handler)
    root.addHandler(recent)
    root.propagate = False


def recent_logs(limit: int = _LOG_CAPACITY) -> tuple[str, ...]:
    """Return the newest bounded application records in chronological order."""
    if limit < 1:
        return ()
    return tuple(_recent_logs)[-limit:]
