"""Tests for Claude API decision engine -- AI-001."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from ai.decision_engine import (
    VALID_ACTIONS,
    AuditEntry,
    CostTracker,
    Decision,
    DecisionAction,
    DecisionEngine,
    build_prompt,
    parse_response,
    validate_decision,
    validate_insight_snapshot,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(**overrides: Any) -> dict[str, Any]:
    base = {
        "market_data": {"prices": {"ETH": "$3,200.00"}},
        "positions": {"open_count": 0, "total_value": "0"},
        "risk_status": {"circuit_breakers_active": False},
        "strategies": [{"id": "STRAT-001", "status": "active"}],
        "recent_decisions": [],
    }
    base.update(overrides)
    return base


def _make_claude_response(
    action: str = "hold",
    strategy: str = "STRAT-001",
    reasoning: str = "Market conditions stable",
    confidence: float = 0.6,
    params: dict | None = None,
) -> str:
    import json
    return json.dumps({
        "action": action,
        "strategy": strategy,
        "reasoning": reasoning,
        "confidence": confidence,
        "params": params or {},
    })


def _make_mock_client(response_text: str = "", input_tokens: int = 100, output_tokens: int = 50):
    """Create a mock Anthropic client that returns a structured response."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_content = MagicMock()
    mock_content.text = response_text or _make_claude_response()
    mock_response.content = [mock_content]
    mock_response.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    mock_client.messages.create.return_value = mock_response
    return mock_client


# ---------------------------------------------------------------------------
# Decision dataclass
# ---------------------------------------------------------------------------

class TestDecision:

    def test_create_with_defaults(self) -> None:
        d = Decision(
            action="hold",
            strategy="STRAT-001",
            reasoning="No action needed",
            confidence=0.5,
        )
        assert d.action == "hold"
        assert d.strategy == "STRAT-001"
        assert d.source == "claude"
        assert d.timestamp  # auto-set

    def test_to_dict(self) -> None:
        d = Decision(
            action="enter",
            strategy="STRAT-002",
            reasoning="Good entry point",
            confidence=0.8,
            params={"amount": "1000"},
        )
        data = d.to_dict()
        assert data["action"] == "enter"
        assert data["params"]["amount"] == "1000"

    def test_from_dict(self) -> None:
        data = {
            "action": "exit",
            "strategy": "STRAT-001",
            "reasoning": "Taking profit",
            "confidence": 0.7,
            "source": "deterministic",
        }
        d = Decision.from_dict(data)
        assert d.action == "exit"
        assert d.source == "deterministic"

    def test_all_actions_valid(self) -> None:
        for action in VALID_ACTIONS:
            d = Decision(
                action=action,
                strategy="test",
                reasoning="test",
                confidence=0.5,
            )
            assert d.action == action

    def test_enum_values(self) -> None:
        assert DecisionAction.HOLD.value == "hold"
        assert DecisionAction.ENTER.value == "enter"
        assert DecisionAction.EXIT.value == "exit"
        assert DecisionAction.ROTATE.value == "rotate"
        assert DecisionAction.ADJUST.value == "adjust"


# ---------------------------------------------------------------------------
# Decision validation
# ---------------------------------------------------------------------------

class TestValidateDecision:

    def test_valid_decision(self) -> None:
        data = {
            "action": "hold",
            "strategy": "STRAT-001",
            "reasoning": "No change",
            "confidence": 0.5,
        }
        valid, errors = validate_decision(data)
        assert valid
        assert not errors

    def test_missing_action(self) -> None:
        data = {"strategy": "STRAT-001", "reasoning": "x", "confidence": 0.5}
        valid, errors = validate_decision(data)
        assert not valid
        assert any("action" in e for e in errors)

    def test_invalid_action(self) -> None:
        data = {
            "action": "yolo",
            "strategy": "STRAT-001",
            "reasoning": "x",
            "confidence": 0.5,
        }
        valid, errors = validate_decision(data)
        assert not valid
        assert any("Invalid action" in e for e in errors)

    def test_missing_strategy(self) -> None:
        data = {"action": "hold", "reasoning": "x", "confidence": 0.5}
        valid, errors = validate_decision(data)
        assert not valid
        assert any("strategy" in e for e in errors)

    def test_missing_reasoning(self) -> None:
        data = {"action": "hold", "strategy": "x", "confidence": 0.5}
        valid, errors = validate_decision(data)
        assert not valid

    def test_missing_confidence(self) -> None:
        data = {"action": "hold", "strategy": "x", "reasoning": "y"}
        valid, errors = validate_decision(data)
        assert not valid

    def test_confidence_out_of_range(self) -> None:
        data = {
            "action": "hold",
            "strategy": "x",
            "reasoning": "y",
            "confidence": 1.5,
        }
        valid, errors = validate_decision(data)
        assert not valid
        assert any("0.0 and 1.0" in e for e in errors)

    def test_confidence_non_numeric(self) -> None:
        data = {
            "action": "hold",
            "strategy": "x",
            "reasoning": "y",
            "confidence": "high",
        }
        valid, errors = validate_decision(data)
        assert not valid


# ---------------------------------------------------------------------------
# Insight snapshot validation
# ---------------------------------------------------------------------------

class TestValidateInsightSnapshot:

    def test_valid_snapshot(self) -> None:
        snapshot = _make_snapshot()
        valid, errors = validate_insight_snapshot(snapshot)
        assert valid
        assert not errors

    def test_missing_field(self) -> None:
        snapshot = _make_snapshot()
        del snapshot["market_data"]
        valid, errors = validate_insight_snapshot(snapshot)
        assert not valid
        assert any("market_data" in e for e in errors)

    def test_all_fields_required(self) -> None:
        required = (
            "market_data", "positions", "risk_status",
            "strategies", "recent_decisions",
        )
        for field_name in required:
            snapshot = _make_snapshot()
            del snapshot[field_name]
            valid, errors = validate_insight_snapshot(snapshot)
            assert not valid, f"Should fail when {field_name} is missing"


# ---------------------------------------------------------------------------
# Prompt construction and response parsing
# ---------------------------------------------------------------------------

class TestPromptConstruction:

    def test_build_prompt_returns_json(self) -> None:
        import json
        snapshot = _make_snapshot()
        prompt = build_prompt(snapshot)
        parsed = json.loads(prompt)
        assert "market_data" in parsed

    def test_build_prompt_handles_decimals(self) -> None:
        snapshot = _make_snapshot(
            positions={"total_value": Decimal("1234.56")},
        )
        prompt = build_prompt(snapshot)
        assert "1234.56" in prompt


class TestResponseParsing:

    def test_parse_valid_json(self) -> None:
        text = '{"action": "hold", "strategy": "STRAT-001"}'
        result = parse_response(text)
        assert result["action"] == "hold"

    def test_parse_with_markdown_code_block(self) -> None:
        text = '```json\n{"action": "enter", "strategy": "STRAT-002"}\n```'
        result = parse_response(text)
        assert result["action"] == "enter"

    def test_parse_with_whitespace(self) -> None:
        text = '\n  {"action": "exit"}\n  '
        result = parse_response(text)
        assert result["action"] == "exit"

    def test_parse_invalid_json_raises(self) -> None:
        import pytest
        with pytest.raises(ValueError, match="Failed to parse"):
            parse_response("not json at all")


# ---------------------------------------------------------------------------
# Cost tracker
# ---------------------------------------------------------------------------

class TestCostTracker:

    def test_initial_state(self) -> None:
        ct = CostTracker(monthly_cap_usd=Decimal("100"))
        assert ct.cumulative_usd == Decimal("0")
        assert ct.call_count == 0
        assert ct.budget_remaining() == Decimal("100")

    def test_record_call_tracks_cost(self) -> None:
        ct = CostTracker(monthly_cap_usd=Decimal("100"))
        cost = ct.record_call(input_tokens=1000, output_tokens=500)
        assert cost > 0
        assert ct.call_count == 1
        assert ct.cumulative_usd == cost

    def test_budget_exhaustion(self) -> None:
        ct = CostTracker(monthly_cap_usd=Decimal("0.001"))
        ct.record_call(input_tokens=1000000, output_tokens=500000)
        assert ct.is_budget_exhausted()
        assert ct.budget_remaining() < Decimal("0")

    def test_budget_not_exhausted(self) -> None:
        ct = CostTracker(monthly_cap_usd=Decimal("100"))
        ct.record_call(input_tokens=100, output_tokens=50)
        assert not ct.is_budget_exhausted()

    def test_multiple_calls_cumulative(self) -> None:
        ct = CostTracker(monthly_cap_usd=Decimal("100"))
        cost1 = ct.record_call(input_tokens=1000, output_tokens=500)
        cost2 = ct.record_call(input_tokens=2000, output_tokens=1000)
        assert ct.call_count == 2
        assert ct.cumulative_usd == cost1 + cost2


# ---------------------------------------------------------------------------
# Decision engine -- core behavior
# ---------------------------------------------------------------------------

class TestDecisionEngineBasic:

    def test_no_client_returns_fallback(self) -> None:
        engine = DecisionEngine(client=None)
        engine._client = None
        decision = engine.decide(_make_snapshot())
        assert decision.action == "hold"
        assert decision.source == "deterministic"
        assert "not configured" in decision.reasoning

    def test_budget_exhausted_returns_fallback(self) -> None:
        mock = _make_mock_client()
        engine = DecisionEngine(client=mock, monthly_cost_cap_usd=Decimal("0"))
        # Exhaust budget - must also set the reset month so _maybe_reset doesn't clear it
        from datetime import UTC, datetime
        engine._cost_tracker._reset_month = datetime.now(UTC).month
        engine._cost_tracker.cumulative_usd = Decimal("100")
        decision = engine.decide(_make_snapshot())
        assert decision.action == "hold"
        assert decision.source == "deterministic"
        assert "budget" in decision.reasoning.lower()

    def test_invalid_snapshot_returns_fallback(self) -> None:
        mock = _make_mock_client()
        engine = DecisionEngine(client=mock)
        decision = engine.decide({"incomplete": True})
        assert decision.action == "hold"
        assert decision.source == "deterministic"


class TestDecisionEngineAPI:

    def test_successful_api_call(self) -> None:
        response = _make_claude_response(
            action="enter",
            strategy="STRAT-001",
            reasoning="Good opportunity",
            confidence=0.75,
        )
        mock = _make_mock_client(response_text=response)
        engine = DecisionEngine(client=mock)
        decision = engine.decide(_make_snapshot())
        assert decision.action == "enter"
        assert decision.strategy == "STRAT-001"
        assert decision.confidence == 0.75
        assert decision.source == "claude"

    def test_api_call_uses_system_prompt(self) -> None:
        mock = _make_mock_client()
        engine = DecisionEngine(client=mock)
        engine.decide(_make_snapshot())
        call_args = mock.messages.create.call_args
        assert "system" in call_args.kwargs
        assert "Icarus" in call_args.kwargs["system"]

    def test_api_call_passes_model(self) -> None:
        mock = _make_mock_client()
        engine = DecisionEngine(client=mock, model="test-model")
        engine.decide(_make_snapshot())
        call_args = mock.messages.create.call_args
        assert call_args.kwargs["model"] == "test-model"

    def test_api_failure_retries_then_fallback(self) -> None:
        mock = MagicMock()
        mock.messages.create.side_effect = Exception("API error")
        engine = DecisionEngine(client=mock, max_retries=2, retry_delay=0.01)
        decision = engine.decide(_make_snapshot())
        assert decision.action == "hold"
        assert decision.source == "deterministic"
        assert mock.messages.create.call_count == 2

    def test_api_returns_invalid_json_fallback(self) -> None:
        mock = _make_mock_client(response_text="not json")
        engine = DecisionEngine(client=mock)
        decision = engine.decide(_make_snapshot())
        assert decision.action == "hold"
        assert decision.source == "deterministic"

    def test_api_returns_invalid_decision_fallback(self) -> None:
        import json
        data = {"action": "yolo", "strategy": "x", "reasoning": "y", "confidence": 0.5}
        response = json.dumps(data)
        mock = _make_mock_client(response_text=response)
        engine = DecisionEngine(client=mock)
        decision = engine.decide(_make_snapshot())
        assert decision.action == "hold"
        assert decision.source == "deterministic"


# ---------------------------------------------------------------------------
# Cost tracking integration
# ---------------------------------------------------------------------------

class TestDecisionEngineCostTracking:

    def test_cost_tracked_on_success(self) -> None:
        mock = _make_mock_client(input_tokens=200, output_tokens=100)
        engine = DecisionEngine(client=mock, monthly_cost_cap_usd=Decimal("100"))
        engine.decide(_make_snapshot())
        assert engine.cost_tracker.call_count == 1
        assert engine.cost_tracker.cumulative_usd > 0

    def test_cost_tracked_on_failure(self) -> None:
        mock = MagicMock()
        mock.messages.create.side_effect = Exception("fail")
        engine = DecisionEngine(client=mock, max_retries=1, retry_delay=0.01)
        engine.decide(_make_snapshot())
        # Cost still recorded even for failed calls (tokens = 0)
        assert engine.cost_tracker.call_count == 1

    def test_cost_cap_from_env(self) -> None:
        with patch.dict("os.environ", {"AI_MONTHLY_COST_CAP_USD": "25"}):
            engine = DecisionEngine(client=MagicMock())
            assert engine.cost_tracker.monthly_cap_usd == Decimal("25")

    def test_default_cost_cap(self) -> None:
        engine = DecisionEngine(client=MagicMock())
        assert engine.cost_tracker.monthly_cap_usd == Decimal("50")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class TestDecisionEngineAuditLog:

    def test_audit_log_recorded_on_success(self) -> None:
        mock = _make_mock_client()
        engine = DecisionEngine(client=mock)
        engine.decide(_make_snapshot())
        assert len(engine.audit_log) == 1
        entry = engine.audit_log[0]
        assert entry.source == "claude"
        assert entry.error is None

    def test_audit_log_recorded_on_fallback(self) -> None:
        mock = MagicMock()
        mock.messages.create.side_effect = Exception("fail")
        engine = DecisionEngine(client=mock, max_retries=1, retry_delay=0.01)
        engine.decide(_make_snapshot())
        assert len(engine.audit_log) == 1
        entry = engine.audit_log[0]
        assert entry.source == "deterministic"
        assert entry.error is not None

    def test_audit_log_capped(self) -> None:
        mock = _make_mock_client()
        engine = DecisionEngine(client=mock, audit_log_size=3)
        for _ in range(5):
            engine.decide(_make_snapshot())
        assert len(engine.audit_log) == 3

    def test_audit_entry_contains_prompt_and_response(self) -> None:
        response = _make_claude_response()
        mock = _make_mock_client(response_text=response)
        engine = DecisionEngine(client=mock)
        engine.decide(_make_snapshot())
        entry = engine.audit_log[0]
        assert entry.prompt  # non-empty
        assert entry.response == response
        assert entry.latency_ms > 0

    def test_audit_entry_to_dict(self) -> None:
        entry = AuditEntry(
            timestamp="2026-01-01T00:00:00",
            prompt="test",
            response="test",
            decision={"action": "hold"},
            cost_usd=Decimal("0.01"),
            latency_ms=100.0,
            source="claude",
        )
        d = entry.to_dict()
        assert d["cost_usd"] == "0.01"
        assert d["source"] == "claude"


# ---------------------------------------------------------------------------
# Schema validation on input/output
# ---------------------------------------------------------------------------

class TestDecisionEngineSchemaValidation:

    def test_validates_snapshot_before_api_call(self) -> None:
        mock = _make_mock_client()
        engine = DecisionEngine(client=mock)
        # Missing required fields
        decision = engine.decide({"market_data": {}})
        assert decision.source == "deterministic"
        # API should not have been called
        mock.messages.create.assert_not_called()

    def test_validates_decision_after_api_call(self) -> None:
        import json
        # Missing confidence
        response = json.dumps({"action": "hold", "strategy": "x", "reasoning": "y"})
        mock = _make_mock_client(response_text=response)
        engine = DecisionEngine(client=mock)
        decision = engine.decide(_make_snapshot())
        assert decision.source == "deterministic"


# ---------------------------------------------------------------------------
# Fallback behavior
# ---------------------------------------------------------------------------

class TestFallbackDecision:

    def test_fallback_is_hold(self) -> None:
        engine = DecisionEngine(client=MagicMock())
        d = engine._fallback_decision("test reason")
        assert d.action == "hold"
        assert d.confidence == 0.0
        assert d.source == "deterministic"
        assert "test reason" in d.reasoning

    def test_fallback_with_strategy(self) -> None:
        engine = DecisionEngine(client=MagicMock())
        d = engine._fallback_decision("reason", strategy="STRAT-002")
        assert d.strategy == "STRAT-002"


# ---------------------------------------------------------------------------
# All decision types
# ---------------------------------------------------------------------------

class TestAllDecisionTypes:

    def test_hold_decision(self) -> None:
        response = _make_claude_response(action="hold", confidence=0.3)
        mock = _make_mock_client(response_text=response)
        engine = DecisionEngine(client=mock)
        d = engine.decide(_make_snapshot())
        assert d.action == "hold"

    def test_enter_decision(self) -> None:
        response = _make_claude_response(action="enter", confidence=0.8)
        mock = _make_mock_client(response_text=response)
        engine = DecisionEngine(client=mock)
        d = engine.decide(_make_snapshot())
        assert d.action == "enter"

    def test_exit_decision(self) -> None:
        response = _make_claude_response(action="exit")
        mock = _make_mock_client(response_text=response)
        engine = DecisionEngine(client=mock)
        d = engine.decide(_make_snapshot())
        assert d.action == "exit"

    def test_rotate_decision(self) -> None:
        response = _make_claude_response(action="rotate")
        mock = _make_mock_client(response_text=response)
        engine = DecisionEngine(client=mock)
        d = engine.decide(_make_snapshot())
        assert d.action == "rotate"

    def test_adjust_decision_with_params(self) -> None:
        response = _make_claude_response(
            action="adjust",
            params={"tick_range": [1000, 2000]},
        )
        mock = _make_mock_client(response_text=response)
        engine = DecisionEngine(client=mock)
        d = engine.decide(_make_snapshot())
        assert d.action == "adjust"
        assert d.params["tick_range"] == [1000, 2000]
