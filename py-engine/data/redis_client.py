"""Redis communication layer — pub/sub, streams, and cache."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from typing import Any

import redis

from validation.schema_validator import validate

CHANNELS = {
    "MARKET_EVENTS": "market:events",
    "EXECUTION_ORDERS": "execution:orders",
    "EXECUTION_RESULTS": "execution:results",
}

CHANNEL_SCHEMA: dict[str, str] = {
    CHANNELS["MARKET_EVENTS"]: "market-events",
    CHANNELS["EXECUTION_ORDERS"]: "execution-orders",
    CHANNELS["EXECUTION_RESULTS"]: "execution-results",
}

SERVICE_NAME = "py-engine"


def _log(event: str, message: str, **kwargs: Any) -> None:
    from datetime import UTC, datetime

    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": SERVICE_NAME,
        "event": event,
        "message": message,
        **kwargs,
    }
    print(json.dumps(entry), flush=True)


class RedisManager:
    """Full Redis manager: pub/sub, streams, and cache."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or os.environ.get("REDIS_URL", "redis://localhost:6379")
        self._client: redis.Redis | None = None
        self._pubsub: redis.client.PubSub | None = None
        self._handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._sub_thread: threading.Thread | None = None
        self._connected = False
        self._stream_max_len = int(os.environ.get("STREAM_MAX_LENGTH", "10000"))

    @property
    def connected(self) -> bool:
        """Check whether the Redis connection is established."""
        return self._connected

    def connect(self) -> None:
        """Connect to Redis with retry."""
        self._client = redis.Redis.from_url(
            self._url,
            decode_responses=True,
            retry_on_timeout=True,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
        self._client.ping()
        self._connected = True
        _log("redis_connected", f"Connected to {self._url}")

    def disconnect(self) -> None:
        """Gracefully disconnect."""
        self._connected = False
        if self._pubsub:
            self._pubsub.unsubscribe()
            self._pubsub.close()
        if self._client:
            self._client.close()
        _log("redis_disconnected", "Disconnected from Redis")

    @property
    def client(self) -> redis.Redis:
        """Return the underlying Redis client instance."""
        if not self._client:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._client

    # ── Pub/Sub ──────────────────────────────────────────

    def publish(self, channel: str, data: dict[str, Any]) -> None:
        """Publish a validated message to a channel + write to stream."""
        schema_name = CHANNEL_SCHEMA.get(channel)
        if schema_name:
            valid, errors = validate(schema_name, data)
            if not valid:
                raise ValueError(
                    f"Cannot publish invalid message to {channel}: {'; '.join(errors)}"
                )

        payload = json.dumps(data)
        self.client.publish(channel, payload)
        # Also write to stream for durability
        self.client.xadd(f"stream:{channel}", {"data": payload}, maxlen=self._stream_max_len, approximate=True)

    def subscribe(
        self,
        channel: str,
        handler: Callable[[dict[str, Any]], None],
    ) -> None:
        """Subscribe to a channel. Messages are validated before delivery."""
        if channel not in self._handlers:
            self._handlers[channel] = []
        self._handlers[channel].append(handler)

        if self._pubsub is None:
            self._pubsub = self.client.pubsub()

        self._pubsub.subscribe(**{channel: self._on_message})

        # Start listener thread if not already running
        if self._sub_thread is None or not self._sub_thread.is_alive():
            self._sub_thread = self._pubsub.run_in_thread(sleep_time=0.01, daemon=True)

    def _on_message(self, message: dict[str, Any]) -> None:
        """Internal message handler for pub/sub."""
        if message["type"] != "message":
            return

        channel = message["channel"]
        raw = message["data"]

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            _log("redis_parse_error", "Failed to parse message JSON", channel=channel)
            return

        schema_name = CHANNEL_SCHEMA.get(channel)
        if schema_name:
            valid, errors = validate(schema_name, data)
            if not valid:
                _log(
                    "schema_validation_error",
                    f"Invalid message on {channel}",
                    channel=channel,
                    errors=errors,
                )
                return

        for handler in self._handlers.get(channel, []):
            handler(data)

    # ── Streams ──────────────────────────────────────────

    def stream_read(
        self,
        channel: str,
        from_id: str = "0-0",
        count: int = 100,
    ) -> list[dict[str, Any]]:
        """Read entries from a stream."""
        entries = self.client.xrange(f"stream:{channel}", from_id, "+", count=count)
        results = []
        for entry_id, fields in entries:
            data = json.loads(fields["data"]) if "data" in fields else fields
            results.append({"id": entry_id, "data": data})
        return results

    def stream_trim(self, channel: str, maxlen: int) -> None:
        """Prune stream entries to approximately maxlen."""
        self.client.xtrim(f"stream:{channel}", maxlen=maxlen, approximate=True)

    # ── Cache ──────────────────────────────────────────

    def cache_set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Set a cached value with TTL."""
        self.client.setex(f"cache:{key}", ttl_seconds, json.dumps(value))

    def cache_get(self, key: str) -> Any | None:
        """Get a cached value. Returns None if expired or missing."""
        raw = self.client.get(f"cache:{key}")
        if raw is None:
            return None
        return json.loads(raw)

    def cache_del(self, key: str) -> None:
        """Delete a cached value."""
        self.client.delete(f"cache:{key}")
