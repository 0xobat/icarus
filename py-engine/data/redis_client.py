"""Redis communication layer — Streams with consumer groups, and cache.

All three channels (market:events, execution:orders, execution:results) use
Redis Streams with XADD/XREADGROUP for reliable, durable message delivery.
Pub/sub is not used. MAXLEN pruning on every write.
"""

from __future__ import annotations

import json
import os
import threading
import time
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

# Default consumer group and consumer name for py-engine
DEFAULT_GROUP = "py-engine"
DEFAULT_CONSUMER = "py-engine-1"


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
    """Redis manager using Streams with consumer groups, plus cache.

    All messaging uses Redis Streams (XADD for publish, XREADGROUP for
    consume). Consumer groups provide reliable delivery with
    acknowledgment. MAXLEN pruning on every write keeps streams bounded.

    Args:
        url: Redis connection URL.
        group: Consumer group name for this service.
        consumer: Consumer name within the group.
        on_connection_loss: Callback when connection is lost.
        on_reconnect: Callback when connection is re-established.
    """

    def __init__(
        self,
        url: str | None = None,
        group: str = DEFAULT_GROUP,
        consumer: str = DEFAULT_CONSUMER,
        on_connection_loss: Callable[[], None] | None = None,
        on_reconnect: Callable[[], None] | None = None,
    ) -> None:
        self._url = url or os.environ.get("REDIS_URL", "redis://localhost:6379")
        self._client: redis.Redis | None = None
        self._handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._reader_threads: dict[str, threading.Thread] = {}
        self._connected = False
        self._stopping = False
        self._stream_max_len = int(os.environ.get("STREAM_MAX_LENGTH", "10000"))
        self._group = group
        self._consumer = consumer
        self._on_connection_loss = on_connection_loss
        self._on_reconnect = on_reconnect
        self._reconnect_backoff = 0.2  # initial backoff in seconds
        self._max_backoff = 30.0

    @property
    def stream_max_len(self) -> int:
        """Return the configured max length for stream pruning."""
        return self._stream_max_len

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
        self._stopping = False
        _log("redis_connected", f"Connected to {self._url}")

    def disconnect(self) -> None:
        """Gracefully disconnect."""
        self._stopping = True
        self._connected = False
        # Wait for reader threads to stop
        for thread in self._reader_threads.values():
            thread.join(timeout=2.0)
        self._reader_threads.clear()
        if self._client:
            self._client.close()
        _log("redis_disconnected", "Disconnected from Redis")

    @property
    def group(self) -> str:
        """Consumer group name."""
        return self._group

    @property
    def consumer(self) -> str:
        """Consumer name within the group."""
        return self._consumer

    @property
    def client(self) -> redis.Redis:
        """Return the underlying Redis client instance."""
        if not self._client:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._client

    def ensure_group(self, channel: str) -> None:
        """Create consumer group for a stream if it doesn't exist.

        Uses MKSTREAM to create the stream if it doesn't exist yet.

        Args:
            channel: The stream/channel name.
        """
        try:
            self.client.xgroup_create(channel, self._group, id="0", mkstream=True)
        except redis.ResponseError as e:
            # Group already exists — that's fine
            if "BUSYGROUP" not in str(e):
                raise

    # ── Streams (publish) ──────────────────────────────────

    def publish(self, channel: str, data: dict[str, Any]) -> None:
        """Publish a validated message to a Redis Stream.

        Validates against the channel's JSON schema, then writes to the
        stream with MAXLEN pruning. No pub/sub is used.

        Args:
            channel: The stream name (e.g. "market:events").
            data: The message payload dict.

        Raises:
            ValueError: If the message fails schema validation.
        """
        schema_name = CHANNEL_SCHEMA.get(channel)
        if schema_name:
            valid, errors = validate(schema_name, data)
            if not valid:
                raise ValueError(
                    f"Cannot publish invalid message to {channel}: {'; '.join(errors)}"
                )

        payload = json.dumps(data)
        self.client.xadd(
            channel, {"data": payload},
            maxlen=self._stream_max_len, approximate=True,
        )

    # ── Streams (subscribe via consumer groups) ────────────

    def subscribe(
        self,
        channel: str,
        handler: Callable[[dict[str, Any]], None],
    ) -> None:
        """Subscribe to a stream using consumer groups.

        Creates the consumer group if needed, then starts a background
        thread that reads new messages via XREADGROUP and delivers them
        to all registered handlers after schema validation.

        Args:
            channel: The stream name to subscribe to.
            handler: Callback receiving validated message dicts.
        """
        if channel not in self._handlers:
            self._handlers[channel] = []
        self._handlers[channel].append(handler)

        # Ensure consumer group exists
        self.ensure_group(channel)

        # Start reader thread if not already running for this channel
        if channel not in self._reader_threads or not self._reader_threads[channel].is_alive():
            thread = threading.Thread(
                target=self._stream_reader,
                args=(channel,),
                daemon=True,
                name=f"stream-reader-{channel}",
            )
            thread.start()
            self._reader_threads[channel] = thread

    def _stream_reader(self, channel: str) -> None:
        """Background thread: read from stream via XREADGROUP.

        Reads new messages (id=">"), validates against schema, delivers
        to handlers, and acknowledges. On connection loss, calls the
        callback and retries with exponential backoff.

        Args:
            channel: The stream name to read from.
        """
        backoff = self._reconnect_backoff

        while not self._stopping:
            try:
                # Block for up to 1 second waiting for new messages
                entries = self.client.xreadgroup(
                    self._group, self._consumer,
                    {channel: ">"},
                    count=100,
                    block=1000,
                )
                # Reset backoff on success
                backoff = self._reconnect_backoff

                if not entries:
                    continue

                for _stream_name, messages in entries:
                    for msg_id, fields in messages:
                        self._process_stream_message(channel, msg_id, fields)

            except (redis.ConnectionError, redis.TimeoutError, OSError) as e:
                if self._stopping:
                    break
                _log(
                    "redis_connection_loss",
                    f"Connection lost on {channel}: {e}",
                    channel=channel,
                )
                if self._on_connection_loss:
                    self._on_connection_loss()

                # Exponential backoff reconnect
                time.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)

                try:
                    self.client.ping()
                    if self._on_reconnect:
                        self._on_reconnect()
                    _log("redis_reconnected", f"Reconnected on {channel}", channel=channel)
                except Exception:
                    pass  # Will retry on next loop iteration

            except Exception as e:
                if self._stopping:
                    break
                _log(
                    "stream_reader_error",
                    f"Unexpected error reading {channel}: {e}",
                    channel=channel,
                )
                time.sleep(1.0)

    def _process_stream_message(
        self, channel: str, msg_id: str, fields: dict[str, str],
    ) -> None:
        """Validate and deliver a single stream message, then acknowledge.

        Args:
            channel: The stream name.
            msg_id: The Redis stream message ID.
            fields: The message fields dict from Redis.
        """
        raw = fields.get("data")
        if not raw:
            # Acknowledge malformed messages so they don't block
            self.client.xack(channel, self._group, msg_id)
            return

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            _log("redis_parse_error", "Failed to parse message JSON", channel=channel)
            self.client.xack(channel, self._group, msg_id)
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
                # Reject: acknowledge so it doesn't redeliver forever
                self.client.xack(channel, self._group, msg_id)
                return

        for handler in self._handlers.get(channel, []):
            handler(data)

        # Acknowledge after successful processing
        self.client.xack(channel, self._group, msg_id)

    # ── Stream utilities ──────────────────────────────────

    def stream_read(
        self,
        channel: str,
        from_id: str = "0-0",
        count: int = 100,
    ) -> list[dict[str, Any]]:
        """Read entries from a stream (raw XRANGE, no consumer group).

        Used for diagnostics, startup recovery, and testing.

        Args:
            channel: The stream name.
            from_id: Start reading from this ID.
            count: Maximum entries to return.

        Returns:
            List of dicts with 'id' and 'data' keys.
        """
        entries = self.client.xrange(channel, from_id, "+", count=count)
        results = []
        for entry_id, fields in entries:
            data = json.loads(fields["data"]) if "data" in fields else fields
            results.append({"id": entry_id, "data": data})
        return results

    def stream_trim(self, channel: str, maxlen: int) -> None:
        """Prune stream entries to approximately maxlen.

        Args:
            channel: The stream name.
            maxlen: Target maximum stream length.
        """
        self.client.xtrim(channel, maxlen=maxlen, approximate=True)

    # ── Cache ──────────────────────────────────────────

    def cache_set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Set a cached value with TTL.

        Args:
            key: Cache key (prefixed with 'cache:' internally).
            value: Value to cache (JSON-serializable).
            ttl_seconds: Time-to-live in seconds.
        """
        self.client.setex(f"cache:{key}", ttl_seconds, json.dumps(value))

    def cache_get(self, key: str) -> Any | None:
        """Get a cached value. Returns None if expired or missing.

        Args:
            key: Cache key (without 'cache:' prefix).

        Returns:
            The cached value, or None.
        """
        raw = self.client.get(f"cache:{key}")
        if raw is None:
            return None
        return json.loads(raw)

    def cache_del(self, key: str) -> None:
        """Delete a cached value.

        Args:
            key: Cache key (without 'cache:' prefix).
        """
        self.client.delete(f"cache:{key}")
