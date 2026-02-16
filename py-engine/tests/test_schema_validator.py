"""Tests for schema validation — sends malformed messages and confirms rejection."""

import pytest

from validation.schema_validator import SchemaValidationError, validate, validate_or_raise


class TestMarketEvents:
    valid_event = {
        "version": "1.0.0",
        "timestamp": "2026-02-16T12:00:00Z",
        "sequence": 1,
        "chain": "ethereum",
        "eventType": "price_update",
        "protocol": "aave_v3",
    }

    def test_accepts_valid_event(self) -> None:
        valid, errors = validate("market-events", self.valid_event)
        assert valid
        assert errors == []

    def test_rejects_missing_required(self) -> None:
        valid, errors = validate("market-events", {"version": "1.0.0"})
        assert not valid
        assert len(errors) > 0

    def test_rejects_invalid_chain(self) -> None:
        valid, _ = validate("market-events", {**self.valid_event, "chain": "polygon"})
        assert not valid

    def test_rejects_invalid_event_type(self) -> None:
        valid, _ = validate("market-events", {**self.valid_event, "eventType": "unknown"})
        assert not valid

    def test_rejects_wrong_version(self) -> None:
        valid, _ = validate("market-events", {**self.valid_event, "version": "2.0.0"})
        assert not valid

    def test_rejects_additional_properties(self) -> None:
        valid, _ = validate("market-events", {**self.valid_event, "extra": "field"})
        assert not valid

    def test_rejects_negative_sequence(self) -> None:
        valid, _ = validate("market-events", {**self.valid_event, "sequence": -1})
        assert not valid


class TestExecutionOrders:
    valid_order = {
        "version": "1.0.0",
        "orderId": "order-123",
        "correlationId": "corr-456",
        "timestamp": "2026-02-16T12:00:00Z",
        "chain": "ethereum",
        "protocol": "aave_v3",
        "action": "supply",
        "params": {"tokenIn": "0xabc", "amount": "1000000000000000000"},
        "limits": {
            "maxGasWei": "50000000000000",
            "maxSlippageBps": 50,
            "deadlineUnix": 1739700000,
        },
    }

    def test_accepts_valid_order(self) -> None:
        valid, errors = validate("execution-orders", self.valid_order)
        assert valid
        assert errors == []

    def test_rejects_missing_limits(self) -> None:
        order = {k: v for k, v in self.valid_order.items() if k != "limits"}
        valid, _ = validate("execution-orders", order)
        assert not valid

    def test_rejects_slippage_over_1000(self) -> None:
        order = {
            **self.valid_order,
            "limits": {**self.valid_order["limits"], "maxSlippageBps": 1500},
        }
        valid, _ = validate("execution-orders", order)
        assert not valid

    def test_rejects_unknown_action(self) -> None:
        valid, _ = validate("execution-orders", {**self.valid_order, "action": "liquidate"})
        assert not valid


class TestExecutionResults:
    valid_result = {
        "version": "1.0.0",
        "orderId": "order-123",
        "correlationId": "corr-456",
        "timestamp": "2026-02-16T12:00:00Z",
        "status": "confirmed",
        "txHash": "0xabc123",
        "blockNumber": 12345,
        "gasUsed": "21000",
    }

    def test_accepts_valid_result(self) -> None:
        valid, errors = validate("execution-results", self.valid_result)
        assert valid
        assert errors == []

    def test_rejects_invalid_status(self) -> None:
        valid, _ = validate("execution-results", {**self.valid_result, "status": "pending"})
        assert not valid

    def test_rejects_missing_order_id(self) -> None:
        result = {k: v for k, v in self.valid_result.items() if k != "orderId"}
        valid, _ = validate("execution-results", result)
        assert not valid


class TestValidateOrRaise:
    def test_does_not_raise_for_valid(self) -> None:
        validate_or_raise(
            "market-events",
            {
                "version": "1.0.0",
                "timestamp": "2026-02-16T12:00:00Z",
                "sequence": 0,
                "chain": "ethereum",
                "eventType": "new_block",
                "protocol": "system",
            },
        )

    def test_raises_with_descriptive_message(self) -> None:
        with pytest.raises(SchemaValidationError, match="Schema validation failed"):
            validate_or_raise("market-events", {})
