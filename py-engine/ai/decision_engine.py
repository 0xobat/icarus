"""Claude API decision engine -- runtime reasoning over market insights (AI-001).

Receives structured insight snapshots, constructs prompts from strategy specs,
invokes the Claude API, and parses responses into typed decision objects.
Includes retry logic, rate limiting, cost tracking, and deterministic fallback.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from monitoring.logger import get_logger

_logger = get_logger("decision-engine", enable_file=False)

# ---------------------------------------------------------------------------
# Decision types
# ---------------------------------------------------------------------------

VALID_ACTIONS = frozenset({"hold", "enter", "exit", "rotate", "adjust"})


class DecisionAction(StrEnum):
    """Typed decision actions the engine can produce."""

    HOLD = "hold"
    ENTER = "enter"
    EXIT = "exit"
    ROTATE = "rotate"
    ADJUST = "adjust"


@dataclass
class Decision:
    """A single trading decision produced by the engine."""

    action: str
    strategy: str
    reasoning: str
    confidence: float
    params: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    source: str = "claude"  # "claude" or "deterministic"

    def __post_init__(self) -> None:
        """Set timestamp if not provided."""
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Decision:
        """Construct a Decision from a dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def validate_decision(decision: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate a decision dict against expected schema.

    Returns:
        Tuple of (valid, errors).
    """
    errors: list[str] = []
    if "action" not in decision:
        errors.append("Missing required field: action")
    elif decision["action"] not in VALID_ACTIONS:
        errors.append(
            f"Invalid action: {decision['action']}. Must be one of {sorted(VALID_ACTIONS)}",
        )
    if "strategy" not in decision:
        errors.append("Missing required field: strategy")
    if "reasoning" not in decision:
        errors.append("Missing required field: reasoning")
    if "confidence" not in decision:
        errors.append("Missing required field: confidence")
    elif not isinstance(decision["confidence"], (int, float)):
        errors.append("confidence must be a number")
    elif not 0.0 <= float(decision["confidence"]) <= 1.0:
        errors.append("confidence must be between 0.0 and 1.0")
    return (len(errors) == 0, errors)


def validate_insight_snapshot(snapshot: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate an insight snapshot dict has the required structure.

    Returns:
        Tuple of (valid, errors).
    """
    errors: list[str] = []
    required = {"market_data", "positions", "risk_status", "strategies", "recent_decisions"}
    for req in required:
        if req not in snapshot:
            errors.append(f"Missing required field: {req}")
    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# Audit log entry
# ---------------------------------------------------------------------------

@dataclass
class AuditEntry:
    """Full record of a decision cycle for debugging."""

    timestamp: str
    prompt: str
    response: str | None
    decision: dict[str, Any]
    cost_usd: Decimal
    latency_ms: float
    source: str  # "claude" or "deterministic"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation."""
        d = asdict(self)
        d["cost_usd"] = str(self.cost_usd)
        return d


# ---------------------------------------------------------------------------
# Cost tracker
# ---------------------------------------------------------------------------

# Anthropic pricing per million tokens (approximate, conservative estimates)
_INPUT_COST_PER_MTOK = Decimal("3.00")
_OUTPUT_COST_PER_MTOK = Decimal("15.00")


@dataclass
class CostTracker:
    """Tracks Claude API costs against a monthly budget cap."""

    monthly_cap_usd: Decimal = Decimal("50")
    cumulative_usd: Decimal = Decimal("0")
    call_count: int = 0
    _reset_month: int = -1

    def _maybe_reset(self) -> None:
        """Reset cumulative cost at the start of each month."""
        current_month = datetime.now(UTC).month
        if self._reset_month != current_month:
            self.cumulative_usd = Decimal("0")
            self.call_count = 0
            self._reset_month = current_month

    def record_call(self, input_tokens: int, output_tokens: int) -> Decimal:
        """Record a Claude API call and return the cost in USD.

        Args:
            input_tokens: Number of input tokens consumed.
            output_tokens: Number of output tokens generated.

        Returns:
            Cost of this call in USD.
        """
        self._maybe_reset()
        input_cost = Decimal(str(input_tokens)) * _INPUT_COST_PER_MTOK / Decimal("1000000")
        output_cost = Decimal(str(output_tokens)) * _OUTPUT_COST_PER_MTOK / Decimal("1000000")
        cost = input_cost + output_cost
        self.cumulative_usd += cost
        self.call_count += 1
        return cost

    def budget_remaining(self) -> Decimal:
        """Return remaining monthly budget in USD."""
        self._maybe_reset()
        return self.monthly_cap_usd - self.cumulative_usd

    def is_budget_exhausted(self) -> bool:
        """Check whether the monthly budget has been exceeded."""
        self._maybe_reset()
        return self.cumulative_usd >= self.monthly_cap_usd


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are the decision engine for Icarus, an autonomous DeFi trading bot.

You receive a market insight snapshot containing:
- Current prices, gas conditions, and protocol metrics
- Active positions and their P&L
- Risk status (circuit breaker states)
- Active strategy specs with their current lifecycle status
- Recent decision history for context

You must respond with a single JSON object representing your trading decision:
{
  "action": "hold" | "enter" | "exit" | "rotate" | "adjust",
  "strategy": "<strategy_id>",
  "reasoning": "<1-2 sentence explanation>",
  "confidence": <0.0 to 1.0>,
  "params": { <optional action-specific parameters> }
}

Decision types:
- hold: No action needed. Market conditions don't warrant changes.
- enter: Open a new position via the specified strategy.
- exit: Close an existing position.
- rotate: Move capital from one position/market to another.
- adjust: Modify parameters of an active position.

Rules:
- Never suggest actions that violate risk limits described in the snapshot.
- Be conservative with confidence scores. Only use >0.8 for very clear signals.
- Prefer "hold" when signals are mixed or unclear.
- Always provide clear, concise reasoning.
- Respond ONLY with the JSON object. No markdown, no explanation outside JSON."""


def build_prompt(snapshot: dict[str, Any]) -> str:
    """Build a user prompt from an insight snapshot.

    Args:
        snapshot: Validated insight snapshot dict.

    Returns:
        Formatted prompt string for Claude API.
    """
    return json.dumps(snapshot, indent=2, default=str)


def parse_response(response_text: str) -> dict[str, Any]:
    """Parse Claude API response into a decision dict.

    Handles common formatting issues like markdown code blocks.

    Args:
        response_text: Raw text response from Claude.

    Returns:
        Parsed decision dict.

    Raises:
        ValueError: If response cannot be parsed as JSON.
    """
    text = response_text.strip()
    # Strip markdown code blocks if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse Claude response as JSON: {e}") from e


# ---------------------------------------------------------------------------
# Decision engine
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "claude-sonnet-4-20250514"
_DEFAULT_MAX_TOKENS = 1024
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_DELAY_SECONDS = 1.0
_DEFAULT_AUDIT_LOG_SIZE = 50


class DecisionEngine:
    """Claude API decision engine with retry, cost tracking, and fallback.

    Constructs structured prompts from insight snapshots, invokes the Claude
    API for reasoning, parses typed decisions, and tracks costs against the
    monthly budget cap. Falls back to deterministic hold decisions when the
    API is unavailable or the budget is exhausted.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_delay: float = _DEFAULT_RETRY_DELAY_SECONDS,
        monthly_cost_cap_usd: Decimal | None = None,
        audit_log_size: int = _DEFAULT_AUDIT_LOG_SIZE,
        client: Any = None,
    ) -> None:
        """Initialize the decision engine.

        Args:
            api_key: Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
            model: Claude model identifier.
            max_tokens: Maximum tokens in response.
            max_retries: Number of retry attempts on failure.
            retry_delay: Base delay between retries in seconds.
            monthly_cost_cap_usd: Monthly spending limit. Defaults to
                AI_MONTHLY_COST_CAP_USD env var or $50.
            audit_log_size: Number of recent decisions to keep in audit log.
            client: Pre-constructed Anthropic client (for testing).
        """
        self._model = model
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._audit_log: deque[AuditEntry] = deque(maxlen=audit_log_size)

        # Cost tracker
        cap_env = os.environ.get("AI_MONTHLY_COST_CAP_USD")
        try:
            cap = monthly_cost_cap_usd or (Decimal(cap_env) if cap_env else Decimal("50"))
        except Exception:
            cap = Decimal("50")
        self._cost_tracker = CostTracker(monthly_cap_usd=cap)

        # Anthropic client -- either injected (for tests) or created from SDK
        if client is not None:
            self._client = client
        else:
            key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            if key:
                import anthropic
                self._client = anthropic.Anthropic(api_key=key)
            else:
                self._client = None

    @property
    def cost_tracker(self) -> CostTracker:
        """Return the cost tracker instance."""
        return self._cost_tracker

    @property
    def audit_log(self) -> list[AuditEntry]:
        """Return recent audit log entries as a list."""
        return list(self._audit_log)

    def _fallback_decision(self, reason: str, strategy: str = "system") -> Decision:
        """Return a deterministic hold decision when Claude API is unavailable.

        Args:
            reason: Why the fallback was triggered.
            strategy: Strategy context for the decision.

        Returns:
            A hold Decision with deterministic source.
        """
        decision = Decision(
            action="hold",
            strategy=strategy,
            reasoning=f"Deterministic fallback: {reason}",
            confidence=0.0,
            source="deterministic",
        )
        _logger.info(
            "Fallback decision issued",
            extra={"data": {"reason": reason, "strategy": strategy}},
        )
        return decision

    def decide(self, snapshot: dict[str, Any]) -> Decision:
        """Make a trading decision based on an insight snapshot.

        Validates the snapshot, checks budget, calls Claude API with retries,
        parses the response, and validates the decision. Falls back to a
        deterministic hold if any step fails.

        Args:
            snapshot: Insight snapshot dict with market_data, positions,
                risk_status, strategies, and recent_decisions fields.

        Returns:
            A validated Decision object.
        """
        # Validate input snapshot
        valid, errors = validate_insight_snapshot(snapshot)
        if not valid:
            _logger.warning(
                "Invalid insight snapshot",
                extra={"data": {"errors": errors}},
            )
            return self._fallback_decision(
                f"Invalid snapshot: {'; '.join(errors)}",
            )

        # Check API availability
        if self._client is None:
            return self._fallback_decision("Claude API client not configured")

        # Check budget
        if self._cost_tracker.is_budget_exhausted():
            return self._fallback_decision(
                f"Monthly budget exhausted (${self._cost_tracker.cumulative_usd}/"
                f"${self._cost_tracker.monthly_cap_usd})",
            )

        # Build prompt
        user_prompt = build_prompt(snapshot)

        # Call Claude API with retries
        start_time = time.monotonic()
        response_text: str | None = None
        last_error: str | None = None
        input_tokens = 0
        output_tokens = 0

        for attempt in range(self._max_retries):
            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                if not response.content:
                    last_error = "Claude returned empty content"
                    continue
                response_text = response.content[0].text
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                break
            except Exception as e:
                last_error = str(e)
                _logger.warning(
                    "Claude API call failed",
                    extra={"data": {
                        "attempt": attempt + 1,
                        "max_retries": self._max_retries,
                        "error": last_error,
                    }},
                )
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (2 ** attempt))

        latency_ms = (time.monotonic() - start_time) * 1000

        # Track cost
        call_cost = self._cost_tracker.record_call(input_tokens, output_tokens)

        if response_text is None:
            decision = self._fallback_decision(
                f"API failed after {self._max_retries} attempts: {last_error}",
            )
            self._log_audit(
                prompt=user_prompt,
                response=None,
                decision=decision.to_dict(),
                cost_usd=call_cost,
                latency_ms=latency_ms,
                source="deterministic",
                error=last_error,
            )
            return decision

        # Parse response
        try:
            decision_dict = parse_response(response_text)
        except ValueError as e:
            decision = self._fallback_decision(f"Response parse failed: {e}")
            self._log_audit(
                prompt=user_prompt,
                response=response_text,
                decision=decision.to_dict(),
                cost_usd=call_cost,
                latency_ms=latency_ms,
                source="deterministic",
                error=str(e),
            )
            return decision

        # Validate decision
        valid, errors = validate_decision(decision_dict)
        if not valid:
            decision = self._fallback_decision(
                f"Invalid decision from Claude: {'; '.join(errors)}",
            )
            self._log_audit(
                prompt=user_prompt,
                response=response_text,
                decision=decision.to_dict(),
                cost_usd=call_cost,
                latency_ms=latency_ms,
                source="deterministic",
                error=f"Validation: {'; '.join(errors)}",
            )
            return decision

        # Build typed decision
        decision = Decision(
            action=decision_dict["action"],
            strategy=decision_dict["strategy"],
            reasoning=decision_dict["reasoning"],
            confidence=float(decision_dict["confidence"]),
            params=decision_dict.get("params", {}),
            source="claude",
        )

        self._log_audit(
            prompt=user_prompt,
            response=response_text,
            decision=decision.to_dict(),
            cost_usd=call_cost,
            latency_ms=latency_ms,
            source="claude",
        )

        _logger.info(
            "Decision made",
            extra={"data": {
                "action": decision.action,
                "strategy": decision.strategy,
                "confidence": decision.confidence,
                "source": "claude",
                "cost_usd": str(call_cost),
            }},
        )

        return decision

    def _log_audit(
        self,
        *,
        prompt: str,
        response: str | None,
        decision: dict[str, Any],
        cost_usd: Decimal,
        latency_ms: float,
        source: str,
        error: str | None = None,
    ) -> None:
        """Append an entry to the audit log."""
        entry = AuditEntry(
            timestamp=datetime.now(UTC).isoformat(),
            prompt=prompt,
            response=response,
            decision=decision,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            source=source,
            error=error,
        )
        self._audit_log.append(entry)
