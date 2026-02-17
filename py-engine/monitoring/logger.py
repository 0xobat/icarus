"""Structured JSON logging with correlation IDs and sensitive data filtering."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ---------------------------------------------------------------------------
# Correlation ID context
# ---------------------------------------------------------------------------
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    """Return the current correlation ID, if set."""
    return _correlation_id.get()


@contextmanager
def correlation_context(correlation_id: str | None = None) -> Generator[str, None, None]:
    """Set a correlation ID for the duration of a block.

    If *correlation_id* is ``None`` a new UUID-4 is generated automatically.
    The previous value is restored when the block exits.
    """
    cid = correlation_id or uuid.uuid4().hex
    token = _correlation_id.set(cid)
    try:
        yield cid
    finally:
        _correlation_id.reset(token)


# ---------------------------------------------------------------------------
# Sensitive data patterns
# ---------------------------------------------------------------------------
# Ethereum private keys: 64 hex chars, optionally prefixed with 0x
_PRIVATE_KEY_RE = re.compile(r"(?:0x)?[0-9a-fA-F]{64}")
# Ethereum addresses: 0x followed by 40 hex chars
_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
# Generic secret env-var names
_SECRET_KEY_NAMES = frozenset({
    "private_key",
    "secret_key",
    "api_key",
    "api_secret",
    "mnemonic",
    "seed_phrase",
})


def _redact_value(value: str) -> str:
    """Redact sensitive patterns from a string value."""
    # Redact private keys first (longer pattern)
    result = _PRIVATE_KEY_RE.sub("[REDACTED_KEY]", value)
    # Truncate wallet addresses to first 6 + last 4 chars
    def _truncate_addr(m: re.Match[str]) -> str:
        addr = m.group(0)
        return f"{addr[:6]}...{addr[-4:]}"

    return _ADDRESS_RE.sub(_truncate_addr, result)


def _redact_dict(d: dict[str, object]) -> dict[str, object]:
    """Recursively redact sensitive data from a dict."""
    out: dict[str, object] = {}
    for key, value in d.items():
        lower_key = key.lower()
        if lower_key in _SECRET_KEY_NAMES:
            out[key] = "[REDACTED]"
        elif isinstance(value, str):
            out[key] = _redact_value(value)
        elif isinstance(value, dict):
            out[key] = _redact_dict(value)  # type: ignore[arg-type]
        elif isinstance(value, list):
            out[key] = [
                _redact_dict(item) if isinstance(item, dict)
                else _redact_value(item) if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------
class StructuredFormatter(logging.Formatter):
    """Format log records as single-line JSON with standard fields."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "service": self.service,
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
        }

        # Attach correlation ID if present
        cid = _correlation_id.get()
        if cid is not None:
            entry["correlationId"] = cid

        # Merge any extra structured data passed via `extra={"data": {...}}`
        extra_data = getattr(record, "data", None)
        if isinstance(extra_data, dict):
            entry["data"] = extra_data

        # Redact sensitive data
        entry = _redact_dict(entry)  # type: ignore[arg-type]

        # Exception info
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


# ---------------------------------------------------------------------------
# Sensitive data filter
# ---------------------------------------------------------------------------
class SensitiveDataFilter(logging.Filter):
    """Scrub sensitive data from log record messages before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_value(record.msg)
        return True


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------
_loggers: dict[str, logging.Logger] = {}

_DEFAULT_LOG_DIR = Path("logs")
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_DEFAULT_BACKUP_COUNT = 5


def get_logger(
    service: str,
    *,
    level: int = logging.DEBUG,
    log_dir: Path | None = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    backup_count: int = _DEFAULT_BACKUP_COUNT,
    enable_file: bool = True,
) -> logging.Logger:
    """Return a configured structured logger for *service*.

    Loggers are cached — calling with the same *service* name returns the
    same logger instance.
    """
    if service in _loggers:
        return _loggers[service]

    logger = logging.getLogger(f"icarus.{service}")
    logger.setLevel(level)
    logger.propagate = False

    formatter = StructuredFormatter(service)

    # Stdout handler
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(SensitiveDataFilter())
    logger.addHandler(stream_handler)

    # Rotating file handler
    if enable_file:
        directory = log_dir or _DEFAULT_LOG_DIR
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            directory / f"{service}.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(SensitiveDataFilter())
        logger.addHandler(file_handler)

    _loggers[service] = logger
    return logger
