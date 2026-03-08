"""Integration tests — schema validation with real schemas and strategy output."""

from __future__ import annotations

from decimal import Decimal

import pytest

from validation.schema_validator import SchemaValidationError, validate, validate_or_raise

# ---------------------------------------------------------------------------
# Valid message fixtures
# ---------------------------------------------------------------------------

VALID_MARKET_EVENT = {
    "version": "1.0.0",
    "timestamp": "2026-02-21T10:00:00Z",
    "sequence": 42,
    "chain": "ethereum",
    "eventType": "rate_change",
    "protocol": "aave_v3",
}

VALID_EXECUTION_ORDER = {
    "version": "1.0.0",
    "orderId": "order-abc-123",
    "correlationId": "corr-xyz-456",
    "timestamp": "2026-02-21T10:00:00Z",
    "chain": "ethereum",
    "protocol": "aave_v3",
    "action": "supply",
    "params": {"tokenIn": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "amount": "1000000"},
    "limits": {
        "maxGasWei": "50000000000000",
        "maxSlippageBps": 50,
        "deadlineUnix": 1740130000,
    },
}

VALID_EXECUTION_RESULT = {
    "version": "1.0.0",
    "orderId": "order-abc-123",
    "correlationId": "corr-xyz-456",
    "timestamp": "2026-02-21T10:01:00Z",
    "status": "confirmed",
    "txHash": "0xdeadbeef1234567890abcdef",
    "blockNumber": 19500000,
    "gasUsed": "21000",
}


# ---------------------------------------------------------------------------
# 1. Valid message acceptance
# ---------------------------------------------------------------------------

class TestValidMessageAcceptance:
    """All three message types pass validation when well-formed."""

    def test_valid_market_event(self) -> None:
        valid, errors = validate("market-events", VALID_MARKET_EVENT)
        assert valid is True
        assert errors == []

    def test_valid_execution_order(self) -> None:
        valid, errors = validate("execution-orders", VALID_EXECUTION_ORDER)
        assert valid is True
        assert errors == []

    def test_valid_execution_result(self) -> None:
        valid, errors = validate("execution-results", VALID_EXECUTION_RESULT)
        assert valid is True
        assert errors == []

    def test_validate_or_raise_passes_valid(self) -> None:
        """validate_or_raise should not raise for valid messages."""
        validate_or_raise("market-events", VALID_MARKET_EVENT)
        validate_or_raise("execution-orders", VALID_EXECUTION_ORDER)
        validate_or_raise("execution-results", VALID_EXECUTION_RESULT)


# ---------------------------------------------------------------------------
# 2. Missing required fields
# ---------------------------------------------------------------------------

class TestMissingRequiredFields:
    """Each schema rejects messages missing required fields."""

    def test_market_event_without_event_type(self) -> None:
        msg = {k: v for k, v in VALID_MARKET_EVENT.items() if k != "eventType"}
        valid, errors = validate("market-events", msg)
        assert valid is False
        assert any("eventType" in e for e in errors)

    def test_execution_order_without_order_id(self) -> None:
        msg = {k: v for k, v in VALID_EXECUTION_ORDER.items() if k != "orderId"}
        valid, errors = validate("execution-orders", msg)
        assert valid is False
        assert any("orderId" in e for e in errors)

    def test_execution_result_without_status(self) -> None:
        msg = {k: v for k, v in VALID_EXECUTION_RESULT.items() if k != "status"}
        valid, errors = validate("execution-results", msg)
        assert valid is False
        assert any("status" in e for e in errors)

    def test_error_messages_descriptive(self) -> None:
        """Error messages should describe what's missing."""
        valid, errors = validate("market-events", {"version": "1.0.0"})
        assert valid is False
        # Should mention at least some of the missing required fields
        error_text = " ".join(errors)
        assert "required" in error_text.lower() or any(
            field in error_text
            for field in ["timestamp", "sequence", "chain", "eventType", "protocol"]
        )


# ---------------------------------------------------------------------------
# 3. Invalid enum values
# ---------------------------------------------------------------------------

class TestInvalidEnumValues:
    """Enum fields reject values not in the allowed set."""

    def test_market_event_invalid_event_type(self) -> None:
        msg = {**VALID_MARKET_EVENT, "eventType": "invalid_type"}
        valid, errors = validate("market-events", msg)
        assert valid is False

    def test_execution_order_invalid_action(self) -> None:
        msg = {**VALID_EXECUTION_ORDER, "action": "invalid_action"}
        valid, errors = validate("execution-orders", msg)
        assert valid is False

    def test_execution_result_invalid_status(self) -> None:
        msg = {**VALID_EXECUTION_RESULT, "status": "maybe"}
        valid, errors = validate("execution-results", msg)
        assert valid is False

    def test_market_event_invalid_chain(self) -> None:
        msg = {**VALID_MARKET_EVENT, "chain": "polygon"}
        valid, errors = validate("market-events", msg)
        assert valid is False

    def test_execution_order_invalid_protocol(self) -> None:
        msg = {**VALID_EXECUTION_ORDER, "protocol": "compound_v2"}
        valid, errors = validate("execution-orders", msg)
        assert valid is False

    def test_validate_or_raise_on_invalid_enum(self) -> None:
        """validate_or_raise raises SchemaValidationError for invalid enums."""
        msg = {**VALID_EXECUTION_RESULT, "status": "maybe"}
        with pytest.raises(SchemaValidationError) as exc_info:
            validate_or_raise("execution-results", msg)
        assert exc_info.value.schema_name == "execution-results"
        assert len(exc_info.value.errors) > 0


# ---------------------------------------------------------------------------
# 4. Type mismatches
# ---------------------------------------------------------------------------

class TestTypeMismatches:
    """Schema rejects values of the wrong type."""

    def test_block_number_as_string(self) -> None:
        msg = {**VALID_EXECUTION_RESULT, "blockNumber": "19500000"}
        valid, errors = validate("execution-results", msg)
        assert valid is False

    def test_sequence_as_string(self) -> None:
        msg = {**VALID_MARKET_EVENT, "sequence": "42"}
        valid, errors = validate("market-events", msg)
        assert valid is False

    def test_max_slippage_as_string(self) -> None:
        msg = {
            **VALID_EXECUTION_ORDER,
            "limits": {**VALID_EXECUTION_ORDER["limits"], "maxSlippageBps": "50"},
        }
        valid, errors = validate("execution-orders", msg)
        assert valid is False

    def test_additional_properties_rejected(self) -> None:
        """All three schemas use additionalProperties: false."""
        msg = {**VALID_MARKET_EVENT, "surprise_field": "oops"}
        valid, errors = validate("market-events", msg)
        assert valid is False


# ---------------------------------------------------------------------------
# 5. Cross-service validation — strategy generates schema-valid orders
# ---------------------------------------------------------------------------

class TestCrossServiceValidation:
    """Prove that AaveLendingStrategy produces valid StrategyReports."""

    def _make_strategy(self):
        """Build AaveLendingStrategy (no dependencies — strategies are analysts)."""
        from strategies.aave_lending import AaveLendingStrategy

        return AaveLendingStrategy()

    def _make_snapshot(self):
        from datetime import UTC, datetime

        from strategies.base import GasInfo, MarketSnapshot, PoolState

        return MarketSnapshot(
            prices=[],
            gas=GasInfo(current_gwei=0.05, avg_24h_gwei=0.05),
            pools=[
                PoolState(
                    protocol="aave_v3",
                    pool_id="USDC",
                    tvl=5_000_000,
                    apy=0.045,
                    utilization=0.80,
                ),
            ],
            timestamp=datetime(2026, 3, 8, 12, 0, 0, tzinfo=UTC),
        )

    def test_strategy_report_has_required_fields(self) -> None:
        """Strategy report includes all required fields."""
        from strategies.base import StrategyReport

        strategy = self._make_strategy()
        report = strategy.evaluate(self._make_snapshot())

        assert isinstance(report, StrategyReport)
        assert report.strategy_id == "LEND-001"
        assert report.timestamp is not None
        assert isinstance(report.observations, list)
        assert isinstance(report.signals, list)

    def test_strategy_report_observations_present(self) -> None:
        """Strategy produces observations about market state."""
        strategy = self._make_strategy()
        report = strategy.evaluate(self._make_snapshot())

        assert len(report.observations) >= 1
        # Gas observation always present
        gas_obs = [o for o in report.observations if o.metric == "gas_current_gwei"]
        assert len(gas_obs) == 1

    def test_strategy_report_entry_signal_on_good_market(self) -> None:
        """Strategy produces entry signal on eligible pool."""
        from strategies.base import SignalType

        strategy = self._make_strategy()
        report = strategy.evaluate(self._make_snapshot())

        entry_signals = [s for s in report.signals if s.type == SignalType.ENTRY_MET]
        assert len(entry_signals) == 1
        assert entry_signals[0].actionable is True

    def test_strategy_report_recommendation_on_entry(self) -> None:
        """Strategy produces supply recommendation when entry conditions met."""
        strategy = self._make_strategy()
        report = strategy.evaluate(self._make_snapshot())

        assert report.recommendation is not None
        assert report.recommendation.action == "supply"
