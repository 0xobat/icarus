"""Dashboard event emitter — publishes lifecycle events to dashboard:events stream.

Emits structured events at key points in the DecisionLoop so the frontend
dashboard can display real-time status. All errors are caught and logged —
event emission never crashes the decision loop.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from monitoring.logger import get_logger
from validation.schema_validator import validate

if TYPE_CHECKING:
    from data.redis_client import RedisManager

_logger = get_logger("event_emitter", enable_file=False)

DASHBOARD_EVENTS_STREAM = "dashboard:events"
DASHBOARD_EVENTS_MAXLEN = 1000


def emit_dashboard_event(
    redis_client: RedisManager,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Publish a lifecycle event to dashboard:events stream with MAXLEN 1000.

    Adds version '1.0.0' and validates against dashboard-events schema.
    All errors caught and logged — never crashes the decision loop.

    Args:
        redis_client: RedisManager instance for stream access.
        event_type: One of the dashboard-events eventType enum values.
        payload: Event-specific data dict.
    """
    event = {
        "version": "1.0.0",
        "timestamp": datetime.now(UTC).isoformat(),
        "eventType": event_type,
        "data": payload,
    }

    valid, errors = validate("dashboard-events", event)
    if not valid:
        _logger.warning(
            "Dashboard event failed schema validation",
            extra={"data": {
                "eventType": event_type,
                "errors": errors,
            }},
        )
        return

    try:
        redis_client.client.xadd(
            DASHBOARD_EVENTS_STREAM,
            {"data": json.dumps(event)},
            maxlen=DASHBOARD_EVENTS_MAXLEN,
            approximate=True,
        )
    except Exception:
        _logger.debug(
            "Failed to publish dashboard event",
            extra={"data": {"eventType": event_type}},
        )
