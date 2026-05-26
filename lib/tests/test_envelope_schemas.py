"""Round-trip test: pydantic envelopes serialize to JSON-Schema-compliant docs.

Locks the Python ↔ TypeScript contract in CI. If anyone changes either an
envelope's pydantic model or the shared/schemas/*.json file without
keeping them in sync, this test fails.

The pytest fixture set covers every realistic shape: per-chain market
events (Base + Solana), allocator-path execution orders, circuit-breaker
orders, and per-chain results.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import jsonschema
import pytest
from icarus.envelopes import (
    BaseChainSpecific,
    ExecutionOrder,
    ExecutionResult,
    MarketEvent,
    OrderLimits,
    OrderParams,
    SolanaChainSpecific,
    SolanaSpecificOrder,
    SolanaSpecificResult,
)

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "shared" / "schemas"


def _load_schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / name).read_text())


@pytest.fixture(scope="module")
def market_event_schema() -> dict:
    return _load_schema("market-event.schema.json")


@pytest.fixture(scope="module")
def execution_order_schema() -> dict:
    return _load_schema("execution-order.schema.json")


@pytest.fixture(scope="module")
def execution_result_schema() -> dict:
    return _load_schema("execution-result.schema.json")


# --- Schemas are themselves valid ---


def test_schemas_are_valid_draft_2020_12(
    market_event_schema, execution_order_schema, execution_result_schema
):
    for schema in (market_event_schema, execution_order_schema, execution_result_schema):
        jsonschema.Draft202012Validator.check_schema(schema)


# --- Market events ---


def test_base_market_event_serializes_to_schema_compliant(market_event_schema):
    ev = MarketEvent(
        timestamp=datetime.now(UTC),
        chain="base",
        sequence=42,
        event_type="swap",
        protocol="aerodrome",
        correlation_id="corr-base-1",
        symbol="USDC/USDbC",
        payload={"amount_in": Decimal("1000")},
        base_specific=BaseChainSpecific(block_number=20_000_000, tx_hash="0xabc"),
    )
    jsonschema.validate(instance=ev.model_dump(mode="json"), schema=market_event_schema)


def test_solana_market_event_serializes_to_schema_compliant(market_event_schema):
    ev = MarketEvent(
        timestamp=datetime.now(UTC),
        chain="solana",
        sequence=17,
        event_type="new_slot",
        protocol="system",
        correlation_id="corr-sol-1",
        solana_specific=SolanaChainSpecific(
            slot=265_000_000,
            block_time=1_748_000_000,
            priority_fee_lamports=5000,
        ),
    )
    jsonschema.validate(instance=ev.model_dump(mode="json"), schema=market_event_schema)


# --- Execution orders ---


def test_allocator_order_serializes_to_schema_compliant(execution_order_schema):
    order = ExecutionOrder(
        order_id="ord-12345abc",
        correlation_id="corr-x",
        timestamp=datetime.now(UTC),
        chain="base",
        protocol="aave_v3",
        action="supply",
        strategy="LEND-001:c-7af3",
        template_id="LEND-001",
        candidate_id="c-7af3",
        params=OrderParams(token_in="USDC", amount=Decimal("1000")),
        limits=OrderLimits(
            max_gas_wei=Decimal("1000000000000"),
            max_slippage_bps=50,
            deadline_unix=1735689600,
        ),
    )
    jsonschema.validate(instance=order.model_dump(mode="json"), schema=execution_order_schema)


def test_solana_order_serializes_to_schema_compliant(execution_order_schema):
    order = ExecutionOrder(
        order_id="ord-sol-9999",
        correlation_id="corr-sol",
        timestamp=datetime.now(UTC),
        chain="solana",
        protocol="kamino",
        action="supply",
        strategy="LEND-KAMINO-001:c-aaaa",
        template_id="LEND-KAMINO-001",
        candidate_id="c-aaaa",
        params=OrderParams(token_in="USDC", amount=Decimal("1000")),
        limits=OrderLimits(
            max_priority_fee_lamports=Decimal("50000"),
            max_slippage_bps=50,
            deadline_unix=1735689600,
        ),
        solana_specific=SolanaSpecificOrder(
            compute_unit_price=10_000,
            compute_unit_limit=200_000,
            lookup_tables=["AddressLookupTable11111111111111111111111111"],
        ),
    )
    jsonschema.validate(instance=order.model_dump(mode="json"), schema=execution_order_schema)


def test_circuit_breaker_order_serializes_to_schema_compliant(execution_order_schema):
    cb = ExecutionOrder(
        order_id="ord-cb-12345",
        correlation_id="cb-corr",
        timestamp=datetime.now(UTC),
        chain="base",
        protocol="aave_v3",
        action="withdraw",
        strategy="CB:drawdown",
        priority="urgent",
        params=OrderParams(token_out="USDC", amount=Decimal("1000000000")),
        limits=OrderLimits(max_slippage_bps=200, deadline_unix=1735689600),
    )
    jsonschema.validate(instance=cb.model_dump(mode="json"), schema=execution_order_schema)


# --- Execution results ---


def test_base_execution_result_serializes_to_schema_compliant(execution_result_schema):
    r = ExecutionResult(
        order_id="ord-12345abc",
        correlation_id="corr-x",
        timestamp=datetime.now(UTC),
        chain="base",
        status="confirmed",
        template_id="LEND-001",
        candidate_id="c-7af3",
        tx_hash="0xdef",
        block_number=20_000_001,
        gas_used_wei=Decimal("150000"),
        effective_gas_price_wei=Decimal("500000000"),
        amount_out=Decimal("1000000000"),
    )
    jsonschema.validate(instance=r.model_dump(mode="json"), schema=execution_result_schema)


def test_solana_execution_result_serializes_to_schema_compliant(execution_result_schema):
    r = ExecutionResult(
        order_id="ord-sol-1234",
        correlation_id="corr-sol",
        timestamp=datetime.now(UTC),
        chain="solana",
        status="confirmed",
        template_id="DRIFT-001",
        candidate_id="c-deadbeef",
        solana_specific=SolanaSpecificResult(
            signature="3xyzABCDEF",
            slot=265_000_001,
            compute_units_consumed=120_000,
            priority_fee_lamports=10_000,
        ),
        amount_out=Decimal("500000000"),
    )
    jsonschema.validate(instance=r.model_dump(mode="json"), schema=execution_result_schema)


def test_failed_result_with_error_serializes_to_schema_compliant(execution_result_schema):
    r = ExecutionResult(
        order_id="ord-fail",
        correlation_id="corr-fail",
        timestamp=datetime.now(UTC),
        chain="base",
        status="rejected_by_guard",
        error="target contract not on allowlist: 0x1234...",
        retry_count=0,
    )
    jsonschema.validate(instance=r.model_dump(mode="json"), schema=execution_result_schema)
