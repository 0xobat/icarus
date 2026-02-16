"""Tests for Redis communication layer. Requires a running Redis instance."""

import os
import time

import pytest

from data.redis_client import CHANNELS, RedisManager

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")


@pytest.fixture(scope="module")
def redis_mgr() -> RedisManager | None:
    mgr = RedisManager(url=REDIS_URL)
    try:
        mgr.connect()
    except Exception:
        return None
    yield mgr
    # Cleanup
    try:
        mgr.client.delete("cache:test-key", "cache:test-ttl")
        mgr.client.delete(f"stream:{CHANNELS['MARKET_EVENTS']}")
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


class TestPubSub:
    def test_publish_and_receive(self, redis_mgr: RedisManager | None) -> None:
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

        # Wait for message delivery
        time.sleep(0.3)

        assert len(received) == 1
        assert received[0]["sequence"] == 42

    def test_rejects_invalid_on_publish(self, redis_mgr: RedisManager | None) -> None:
        requires_redis(redis_mgr)
        assert redis_mgr is not None

        with pytest.raises(ValueError, match="Cannot publish invalid message"):
            redis_mgr.publish(CHANNELS["MARKET_EVENTS"], {"invalid": True})


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


class TestStreams:
    def test_writes_to_stream_on_publish(self, redis_mgr: RedisManager | None) -> None:
        requires_redis(redis_mgr)
        assert redis_mgr is not None

        entries = redis_mgr.stream_read(CHANNELS["MARKET_EVENTS"])
        assert len(entries) > 0
        assert entries[0]["data"]["version"] == "1.0.0"

    def test_trim_stream(self, redis_mgr: RedisManager | None) -> None:
        requires_redis(redis_mgr)
        assert redis_mgr is not None

        redis_mgr.stream_trim(CHANNELS["MARKET_EVENTS"], 1)
        entries = redis_mgr.stream_read(CHANNELS["MARKET_EVENTS"])
        assert len(entries) <= 2
