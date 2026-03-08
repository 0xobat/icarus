"""Tests for Redis communication layer. Requires a running Redis instance."""

import os
import time

import pytest

from data.redis_client import CHANNELS, RedisManager

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")


@pytest.fixture(scope="module")
def redis_mgr() -> RedisManager | None:
    mgr = RedisManager(url=REDIS_URL, group="test-group", consumer="test-consumer-1")
    try:
        mgr.connect()
    except Exception:
        yield None
        return
    yield mgr
    # Cleanup
    try:
        mgr.client.delete("cache:test-key", "cache:test-ttl")
        mgr.client.delete(CHANNELS["MARKET_EVENTS"])
    except Exception:
        pass
    mgr.disconnect()


def requires_redis(redis_mgr: RedisManager | None) -> None:
    if redis_mgr is None:
        pytest.skip("Redis not available")


class TestConnection:
    def test_connects_successfully(self, redis_mgr: RedisManager | None) -> None:
        requires_redis(redis_mgr)
        assert redis_mgr is not None
        assert redis_mgr.connected


class TestStreamsPublishSubscribe:
    """Tests for Redis Streams-based publish/subscribe via consumer groups."""

    def test_publish_and_receive_via_streams(self, redis_mgr: RedisManager | None) -> None:
        requires_redis(redis_mgr)
        assert redis_mgr is not None

        received: list[dict] = []
        redis_mgr.subscribe(CHANNELS["MARKET_EVENTS"], lambda data: received.append(data))

        event = {
            "version": "1.0.0",
            "timestamp": "2026-02-16T12:00:00Z",
            "sequence": 42,
            "chain": "ethereum",
            "eventType": "new_block",
            "protocol": "system",
            "blockNumber": 12345,
        }
        redis_mgr.publish(CHANNELS["MARKET_EVENTS"], event)

        # Wait for stream reader to pick up the message
        time.sleep(1.5)

        assert len(received) == 1
        assert received[0]["sequence"] == 42

    def test_rejects_invalid_on_publish(self, redis_mgr: RedisManager | None) -> None:
        requires_redis(redis_mgr)
        assert redis_mgr is not None

        with pytest.raises(ValueError, match="Cannot publish invalid message"):
            redis_mgr.publish(CHANNELS["MARKET_EVENTS"], {"invalid": True})

    def test_publish_writes_to_stream(self, redis_mgr: RedisManager | None) -> None:
        """Publish writes directly to the stream (no stream: prefix)."""
        requires_redis(redis_mgr)
        assert redis_mgr is not None

        entries = redis_mgr.stream_read(CHANNELS["MARKET_EVENTS"])
        assert len(entries) > 0
        assert entries[0]["data"]["version"] == "1.0.0"

    def test_maxlen_pruning(self, redis_mgr: RedisManager | None) -> None:
        """Stream trim prunes entries."""
        requires_redis(redis_mgr)
        assert redis_mgr is not None

        redis_mgr.stream_trim(CHANNELS["MARKET_EVENTS"], 1)
        entries = redis_mgr.stream_read(CHANNELS["MARKET_EVENTS"])
        assert len(entries) <= 2


class TestCache:
    def test_set_and_get(self, redis_mgr: RedisManager | None) -> None:
        requires_redis(redis_mgr)
        assert redis_mgr is not None

        redis_mgr.cache_set("test-key", {"price": 1234.56}, 60)
        result = redis_mgr.cache_get("test-key")
        assert result == {"price": 1234.56}

    def test_returns_none_for_missing(self, redis_mgr: RedisManager | None) -> None:
        requires_redis(redis_mgr)
        assert redis_mgr is not None

        result = redis_mgr.cache_get("nonexistent-key")
        assert result is None

    def test_delete(self, redis_mgr: RedisManager | None) -> None:
        requires_redis(redis_mgr)
        assert redis_mgr is not None

        redis_mgr.cache_set("test-ttl", "value", 60)
        redis_mgr.cache_del("test-ttl")
        assert redis_mgr.cache_get("test-ttl") is None


class TestConsumerGroups:
    """Tests for consumer group creation and management."""

    def test_ensure_group_creates_group(self, redis_mgr: RedisManager | None) -> None:
        requires_redis(redis_mgr)
        assert redis_mgr is not None

        # ensure_group is called internally by subscribe, but test it directly
        test_stream = "test:consumer:group"
        try:
            redis_mgr.ensure_group(test_stream)
            # Verify the group exists
            groups = redis_mgr.client.xinfo_groups(test_stream)
            assert any(g["name"] == redis_mgr.group for g in groups)
        finally:
            redis_mgr.client.delete(test_stream)

    def test_ensure_group_idempotent(self, redis_mgr: RedisManager | None) -> None:
        requires_redis(redis_mgr)
        assert redis_mgr is not None

        test_stream = "test:consumer:idempotent"
        try:
            redis_mgr.ensure_group(test_stream)
            # Should not raise on second call
            redis_mgr.ensure_group(test_stream)
        finally:
            redis_mgr.client.delete(test_stream)


class TestConnectionCallbacks:
    """Tests for connection loss and reconnect callbacks."""

    def test_callbacks_are_stored(self) -> None:
        loss_called = False
        reconnect_called = False

        def on_loss() -> None:
            nonlocal loss_called
            loss_called = True

        def on_reconnect() -> None:
            nonlocal reconnect_called
            reconnect_called = True

        mgr = RedisManager(
            url=REDIS_URL,
            on_connection_loss=on_loss,
            on_reconnect=on_reconnect,
        )
        assert mgr._on_connection_loss is on_loss
        assert mgr._on_reconnect is on_reconnect

    def test_stream_max_length_from_env(self) -> None:
        """STREAM_MAX_LENGTH env var is respected."""
        original = os.environ.get("STREAM_MAX_LENGTH")
        try:
            os.environ["STREAM_MAX_LENGTH"] = "5000"
            mgr = RedisManager(url=REDIS_URL)
            assert mgr._stream_max_len == 5000
        finally:
            if original is not None:
                os.environ["STREAM_MAX_LENGTH"] = original
            else:
                os.environ.pop("STREAM_MAX_LENGTH", None)


class TestSchemaValidationOnConsume:
    """Tests that schema violations are rejected with structured logs on consume."""

    def test_invalid_message_not_delivered(self, redis_mgr: RedisManager | None) -> None:
        requires_redis(redis_mgr)
        assert redis_mgr is not None

        received: list[dict] = []
        channel = CHANNELS["EXECUTION_ORDERS"]
        redis_mgr.subscribe(channel, lambda data: received.append(data))

        # Write an invalid message directly to the stream (bypass publish validation)
        redis_mgr.client.xadd(channel, {"data": '{"invalid": true}'})

        time.sleep(1.5)

        # Invalid message should NOT be delivered to handler
        assert len(received) == 0
