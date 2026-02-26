"""Crypto agent Python engine — decision-making service."""

import json
import signal
import sys
import time
from datetime import UTC, datetime

SERVICE_NAME = "py-engine"

_shutdown = False


def _handle_signal(sig: int, _frame: object) -> None:
    global _shutdown  # noqa: PLW0603
    _shutdown = True


def log(event: str, message: str, **kwargs: object) -> None:
    """Structured JSON logger."""
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": SERVICE_NAME,
        "event": event,
        "message": message,
        **kwargs,
    }
    print(json.dumps(entry), flush=True)


def main() -> None:
    """Run the Python engine main loop."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log("startup", "Python engine starting...")

    # TODO: Initialize Redis connection (INFRA-002)
    # TODO: Initialize data pipeline (DATA-001)
    # TODO: Initialize strategy engine (STRAT-001)
    # TODO: Initialize risk manager (RISK-001)

    log("ready", "Python engine ready")

    while not _shutdown:
        time.sleep(1)

    log("shutdown", "Python engine shutting down")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("fatal_error", str(e))
        sys.exit(1)
