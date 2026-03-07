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
    """Prove that AaveLendingStrategy output passes execution-orders schema."""

    def _make_strategy(self):
        """Build AaveLendingStrategy with minimal mocked dependencies."""
        from portfolio.allocator import PortfolioAllocator
        from portfolio.position_tracker import PositionTracker
        from strategies.aave_lending import AaveLendingConfig, AaveLendingStrategy

        allocator = PortfolioAllocator(
            total_capital=Decimal("10000"),
            positions={},
        )
        tracker = PositionTracker()
        config = AaveLendingConfig(
            min_position_value_usd=Decimal("50"),
        )
        return AaveLendingStrategy(allocator, tracker, config)

    def _make_markets(self):
        from strategies.aave_lending import AaveMarket

        return [
            AaveMarket(
                asset="USDC",
                supply_apy=Decimal("0.045"),
                available_liquidity=Decimal("1000000"),
                utilization_rate=Decimal("0.80"),
                chain="base",
            ),
        ]

    def test_generated_order_passes_schema(self) -> None:
        """An order from AaveLendingStrategy validates against execution-orders schema."""
        strategy = self._make_strategy()
        markets = self._make_markets()
        orders = strategy.generate_orders(markets, correlation_id="test-corr-001")

        assert len(orders) >= 1, "Strategy should generate at least one supply order"

        for order in orders:
            valid, errors = validate("execution-orders", order)
            assert valid is True, (
                f"Strategy-generated order failed schema validation: {errors}"
            )

    def test_generated_order_has_required_fields(self) -> None:
        """Strategy output includes all fields the schema requires."""
        strategy = self._make_strategy()
        orders = strategy.generate_orders(self._make_markets())

        assert len(orders) >= 1
        order = orders[0]

        required_fields = [
            "version", "orderId", "correlationId", "timestamp",
            "chain", "protocol", "action", "params", "limits",
        ]
        for field in required_fields:
            assert field in order, f"Missing required field: {field}"

    def test_generated_order_limits_valid(self) -> None:
        """Strategy output limits pass schema constraints."""
        strategy = self._make_strategy()
        orders = strategy.generate_orders(self._make_markets())

        assert len(orders) >= 1
        limits = orders[0]["limits"]

        assert isinstance(limits["maxSlippageBps"], int)
        assert 0 <= limits["maxSlippageBps"] <= 1000
        assert isinstance(limits["deadlineUnix"], int)
        assert isinstance(limits["maxGasWei"], str)

    def test_validate_or_raise_passes_for_strategy_output(self) -> None:
        """validate_or_raise does not throw for strategy-generated orders."""
        strategy = self._make_strategy()
        orders = strategy.generate_orders(self._make_markets())

        assert len(orders) >= 1
        for order in orders:
            validate_or_raise("execution-orders", order)
