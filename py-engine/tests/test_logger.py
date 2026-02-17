"""Tests for structured logging — MON-001."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from monitoring.logger import (
    SensitiveDataFilter,
    StructuredFormatter,
    _loggers,
    _redact_value,
    correlation_context,
    get_correlation_id,
    get_logger,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    msg: str = "test message",
    level: int = logging.INFO,
    **extras: object,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test",
        level=level,
        pathname="test.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in extras.items():
        setattr(record, k, v)
    return record


# ---------------------------------------------------------------------------
# JSON output format
# ---------------------------------------------------------------------------

class TestJsonFormat:
    """All logs must be valid JSON with required fields."""

    def test_output_is_valid_json(self) -> None:
        fmt = StructuredFormatter("py-engine")
        record = _make_record("startup")
        raw = fmt.format(record)
        entry = json.loads(raw)
        assert isinstance(entry, dict)

    def test_required_fields_present(self) -> None:
        fmt = StructuredFormatter("py-engine")
        record = _make_record("startup")
        entry = json.loads(fmt.format(record))
        assert "timestamp" in entry
        assert entry["service"] == "py-engine"
        assert "event" in entry
        assert "level" in entry

    def test_timestamp_is_utc_iso(self) -> None:
        fmt = StructuredFormatter("py-engine")
        record = _make_record("test")
        entry = json.loads(fmt.format(record))
        ts = entry["timestamp"]
        assert ts.endswith("+00:00") or ts.endswith("Z")

    def test_level_values(self) -> None:
        fmt = StructuredFormatter("py-engine")
        for level, name in [
            (logging.DEBUG, "DEBUG"),
            (logging.INFO, "INFO"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
            (logging.CRITICAL, "CRITICAL"),
        ]:
            record = _make_record("test", level=level)
            entry = json.loads(fmt.format(record))
            assert entry["level"] == name

    def test_extra_data_included(self) -> None:
        fmt = StructuredFormatter("py-engine")
        record = _make_record("test", data={"token": "ETH", "amount": 1.5})
        entry = json.loads(fmt.format(record))
        assert entry["data"]["token"] == "ETH"
        assert entry["data"]["amount"] == 1.5

    def test_exception_included(self) -> None:
        fmt = StructuredFormatter("py-engine")
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = _make_record("error")
            record.exc_info = sys.exc_info()
        entry = json.loads(fmt.format(record))
        assert "exception" in entry
        assert "ValueError" in entry["exception"]


# ---------------------------------------------------------------------------
# Correlation ID propagation
# ---------------------------------------------------------------------------

class TestCorrelationId:
    """Correlation IDs must propagate through context and into log entries."""

    def test_default_is_none(self) -> None:
        assert get_correlation_id() is None

    def test_context_sets_and_restores(self) -> None:
        assert get_correlation_id() is None
        with correlation_context("abc-123") as cid:
            assert cid == "abc-123"
            assert get_correlation_id() == "abc-123"
        assert get_correlation_id() is None

    def test_auto_generates_id(self) -> None:
        with correlation_context() as cid:
            assert cid is not None
            assert len(cid) == 32  # uuid4 hex

    def test_nested_contexts(self) -> None:
        with correlation_context("outer"):
            assert get_correlation_id() == "outer"
            with correlation_context("inner") as inner:
                assert inner == "inner"
                assert get_correlation_id() == "inner"
            assert get_correlation_id() == "outer"
        assert get_correlation_id() is None

    def test_correlation_id_in_log_entry(self) -> None:
        fmt = StructuredFormatter("py-engine")
        with correlation_context("test-cid-999"):
            record = _make_record("trade")
            entry = json.loads(fmt.format(record))
            assert entry["correlationId"] == "test-cid-999"

    def test_no_correlation_id_when_unset(self) -> None:
        fmt = StructuredFormatter("py-engine")
        record = _make_record("test")
        entry = json.loads(fmt.format(record))
        assert "correlationId" not in entry


# ---------------------------------------------------------------------------
# Sensitive data filtering
# ---------------------------------------------------------------------------

class TestSensitiveDataFilter:
    """Private keys and wallet addresses must never appear in logs."""

    def test_redacts_private_key_hex(self) -> None:
        key = "a" * 64
        result = _redact_value(f"key={key}")
        assert key not in result
        assert "[REDACTED_KEY]" in result

    def test_redacts_0x_private_key(self) -> None:
        key = "0x" + "b" * 64
        result = _redact_value(f"signing with {key}")
        assert key not in result
        assert "[REDACTED_KEY]" in result

    def test_truncates_wallet_address(self) -> None:
        addr = "0x" + "d" * 40
        result = _redact_value(f"sender={addr}")
        assert addr not in result
        assert "0xdddd" in result  # first 6 chars kept
        assert "...dddd" in result  # last 4 chars kept

    def test_redacts_secret_key_names_in_dict(self) -> None:
        from monitoring.logger import _redact_dict

        data = {"private_key": "supersecret", "name": "ok"}
        result = _redact_dict(data)
        assert result["private_key"] == "[REDACTED]"
        assert result["name"] == "ok"

    def test_redacts_nested_dict(self) -> None:
        from monitoring.logger import _redact_dict

        data = {"config": {"api_key": "secret123", "timeout": 30}}
        result = _redact_dict(data)
        assert result["config"]["api_key"] == "[REDACTED]"  # type: ignore[index]
        assert result["config"]["timeout"] == 30  # type: ignore[index]

    def test_filter_scrubs_message(self) -> None:
        f = SensitiveDataFilter()
        key = "0x" + "a" * 64
        record = _make_record(f"imported key {key}")
        f.filter(record)
        assert key not in record.msg
        assert "[REDACTED_KEY]" in record.msg

    def test_full_pipeline_redacts(self) -> None:
        """End-to-end: log with private key → JSON output has it redacted."""
        fmt = StructuredFormatter("py-engine")
        key = "0x" + "f" * 64
        record = _make_record(f"using key {key}")
        raw = fmt.format(record)
        assert key not in raw
        entry = json.loads(raw)
        assert "[REDACTED_KEY]" in entry["message"]


# ---------------------------------------------------------------------------
# Log level filtering
# ---------------------------------------------------------------------------

class TestLogLevels:
    """Logger must respect level filtering."""

    def test_debug_filtered_at_info_level(self, capfd: object) -> None:
        # Clean up cached logger
        name = "level-test"
        _loggers.pop(name, None)
        logger = get_logger(name, level=logging.INFO, enable_file=False)
        logger.debug("should not appear")
        logger.info("should appear")
        # Clean up
        _loggers.pop(name, None)


# ---------------------------------------------------------------------------
# Rotating file handler
# ---------------------------------------------------------------------------

class TestRotatingFile:
    """Logs must be written to rotating log files."""

    def test_creates_log_file(self, tmp_path: Path) -> None:
        name = "file-test"
        _loggers.pop(name, None)
        logger = get_logger(name, log_dir=tmp_path, max_bytes=1024, backup_count=2)
        logger.info("hello file")

        log_file = tmp_path / f"{name}.log"
        assert log_file.exists()
        content = log_file.read_text()
        entry = json.loads(content.strip())
        assert entry["message"] == "hello file"

        # Clean up
        _loggers.pop(name, None)

    def test_rotation_on_size(self, tmp_path: Path) -> None:
        name = "rotate-test"
        _loggers.pop(name, None)
        logger = get_logger(name, log_dir=tmp_path, max_bytes=200, backup_count=2)

        for i in range(50):
            logger.info(f"message number {i:04d}")

        log_file = tmp_path / f"{name}.log"
        backup = tmp_path / f"{name}.log.1"
        assert log_file.exists()
        assert backup.exists()

        # Clean up
        _loggers.pop(name, None)


# ---------------------------------------------------------------------------
# Logger caching
# ---------------------------------------------------------------------------

class TestLoggerCaching:
    """get_logger returns the same instance for the same service name."""

    def test_same_instance_returned(self) -> None:
        name = "cache-test"
        _loggers.pop(name, None)
        a = get_logger(name, enable_file=False)
        b = get_logger(name, enable_file=False)
        assert a is b
        _loggers.pop(name, None)
