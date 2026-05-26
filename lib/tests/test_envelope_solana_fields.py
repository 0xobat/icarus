"""W7 — Solana envelope extensions: round-trip + chain-discriminator tests.

Every chain-discriminated envelope (MarketEvent, ExecutionOrder,
ExecutionResult) must satisfy two contracts simultaneously:

  1. The pydantic model accepts the producer dict.
  2. The JSON Schema in shared/schemas/*.json accepts the same dict.

W2 commit 0441958 established the cross-validation pattern for
MarketEvent. W7 extends it to the order/result envelopes so the new
solana-executor can publish/consume valid v2 envelopes without the
Python producer drifting from the TypeScript consumer.

The five tests here are the W7 acceptance gate:

  (a) ExecutionOrder chain="solana" + valid solana_specific → both accept
  (b) ExecutionOrder chain="solana" but base_specific-style violation → both reject
  (c) ExecutionResult chain="solana" + signature + slot → both accept
  (d) MarketEvent chain="solana" + slot + block_time → both accept
  (e) Round-trip: ExecutionOrder.model_dump_json() → re-parse → equal
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import jsonschema
import pytest
from icarus.envelopes import (
    ExecutionOrder,
    ExecutionResult,
    MarketEvent,
    OrderLimits,
    OrderParams,
    SolanaChainSpecific,
    SolanaSpecificOrder,
    SolanaSpecificResult,
)
from pydantic import ValidationError

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "shared" / "schemas"


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / name).read_text())


@pytest.fixture(scope="module")
def order_schema() -> dict:
    return _schema("execution-order.schema.json")


@pytest.fixture(scope="module")
def result_schema() -> dict:
    return _schema("execution-result.schema.json")


@pytest.fixture(scope="module")
def market_event_schema() -> dict:
    return _schema("market-event.schema.json")


# ── (a) Solana order with solana_specific is accepted by both validators ───────


def test_solana_order_accepted_by_pydantic_and_jsonschema(order_schema):
    order = ExecutionOrder(
        order_id="ord-sol-0001",
        correlation_id="corr-sol-a",
        timestamp=datetime.now(UTC),
        chain="solana",
        protocol="kamino",
        action="supply",
        strategy="LEND-KAMINO-001:c-7af3",
        template_id="LEND-KAMINO-001",
        candidate_id="c-7af3",
        params=OrderParams(token_in="USDC", amount=Decimal("1000")),
        limits=OrderLimits(
            max_priority_fee_lamports=Decimal("50000"),
            max_slippage_bps=50,
            deadline_unix=1_735_689_600,
        ),
        solana_specific=SolanaSpecificOrder(
            compute_unit_price=10_000,
            compute_unit_limit=200_000,
            lookup_tables=["AddrLookupTbl1111111111111111111111111111111"],
        ),
    )
    assert order.solana_specific is not None
    assert order.solana_specific.compute_unit_price == 10_000

    # JSON Schema must accept the same doc.
    jsonschema.validate(instance=order.model_dump(mode="json"), schema=order_schema)


# ── (b) Solana order missing solana_specific is rejected by both ───────────────


def test_solana_order_without_solana_specific_rejected_by_both(order_schema):
    """pydantic rejects construction; an equivalently-shaped dict (built by
    hand to skirt pydantic) must also fail JSON Schema validation."""

    # Pydantic side.
    with pytest.raises(ValidationError) as exc:
        ExecutionOrder(
            order_id="ord-sol-0002",
            correlation_id="corr-sol-b",
            timestamp=datetime.now(UTC),
            chain="solana",
            protocol="kamino",
            action="supply",
            strategy="CB:drawdown",
            params=OrderParams(token_in="USDC", amount=Decimal("1000")),
            limits=OrderLimits(
                max_priority_fee_lamports=Decimal("50000"),
                max_slippage_bps=50,
                deadline_unix=1_735_689_600,
            ),
        )
    assert "solana_specific" in str(exc.value)

    # JSON Schema side — same dict, no `solana_specific` field.
    doc = {
        "version": "1.0.0",
        "order_id": "ord-sol-0002",
        "correlation_id": "corr-sol-b",
        "timestamp": "2026-05-25T00:00:00Z",
        "chain": "solana",
        "protocol": "kamino",
        "action": "supply",
        "strategy": "CB:drawdown",
        "priority": "normal",
        "params": {"token_in": "USDC", "amount": "1000"},
        "limits": {
            "max_priority_fee_lamports": "50000",
            "max_slippage_bps": 50,
            "deadline_unix": 1_735_689_600,
        },
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=doc, schema=order_schema)


def test_base_order_with_solana_specific_rejected_by_both(order_schema):
    """The mirror direction: a Base order MUST NOT carry solana_specific."""

    with pytest.raises(ValidationError) as exc:
        ExecutionOrder(
            order_id="ord-base-0003",
            correlation_id="corr-base-c",
            timestamp=datetime.now(UTC),
            chain="base",
            protocol="aave_v3",
            action="supply",
            strategy="LEND-001:c-bbbb",
            template_id="LEND-001",
            candidate_id="c-bbbb",
            params=OrderParams(token_in="USDC", amount=Decimal("1000")),
            limits=OrderLimits(
                max_gas_wei=Decimal("1000000000000"),
                max_slippage_bps=50,
                deadline_unix=1_735_689_600,
            ),
            solana_specific=SolanaSpecificOrder(compute_unit_price=1),
        )
    assert "solana_specific" in str(exc.value)

    # Same shape as a dict — JSON Schema also rejects (allOf forces null).
    doc = {
        "version": "1.0.0",
        "order_id": "ord-base-0003",
        "correlation_id": "corr-base-c",
        "timestamp": "2026-05-25T00:00:00Z",
        "chain": "base",
        "protocol": "aave_v3",
        "action": "supply",
        "strategy": "LEND-001:c-bbbb",
        "template_id": "LEND-001",
        "candidate_id": "c-bbbb",
        "priority": "normal",
        "params": {"token_in": "USDC", "amount": "1000"},
        "limits": {
            "max_gas_wei": "1000000000000",
            "max_slippage_bps": 50,
            "deadline_unix": 1_735_689_600,
        },
        "solana_specific": {
            "compute_unit_price": 1,
            "compute_unit_limit": None,
            "lookup_tables": [],
        },
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=doc, schema=order_schema)


# ── (c) Solana result with signature + slot ───────────────────────────────────


def test_solana_result_with_signature_and_slot_accepted_by_both(result_schema):
    r = ExecutionResult(
        order_id="ord-sol-0004",
        correlation_id="corr-sol-d",
        timestamp=datetime.now(UTC),
        chain="solana",
        status="confirmed",
        template_id="LEND-KAMINO-001",
        candidate_id="c-aaaa",
        amount_out=Decimal("999000"),
        solana_specific=SolanaSpecificResult(
            signature="5J3WgxL7Z2YzfQ8d3aSig11111111111111111111111111111111111111111111111111111111111111111",
            slot=265_000_001,
            compute_units_consumed=145_000,
            priority_fee_lamports=12_345,
        ),
    )
    assert r.solana_specific is not None
    assert r.solana_specific.slot == 265_000_001
    jsonschema.validate(instance=r.model_dump(mode="json"), schema=result_schema)


def test_solana_result_without_solana_specific_rejected_by_both(result_schema):
    with pytest.raises(ValidationError) as exc:
        ExecutionResult(
            order_id="ord-sol-0005",
            correlation_id="corr-sol-e",
            timestamp=datetime.now(UTC),
            chain="solana",
            status="confirmed",
        )
    assert "solana_specific" in str(exc.value)

    doc = {
        "version": "1.0.0",
        "order_id": "ord-sol-0005",
        "correlation_id": "corr-sol-e",
        "timestamp": "2026-05-25T00:00:00Z",
        "chain": "solana",
        "status": "confirmed",
        "retry_count": 0,
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=doc, schema=result_schema)


# ── (d) Solana MarketEvent with slot + block_time ──────────────────────────────


def test_solana_market_event_with_slot_and_block_time_accepted_by_both(
    market_event_schema,
):
    ev = MarketEvent(
        timestamp=datetime.now(UTC),
        chain="solana",
        sequence=42,
        event_type="price_update",
        protocol="jupiter",
        correlation_id="corr-sol-f",
        symbol="SOL/USDC",
        payload={"price": Decimal("150.42")},
        solana_specific=SolanaChainSpecific(
            slot=265_000_000,
            block_time=1_748_000_000,
            priority_fee_lamports=5_000,
        ),
    )
    assert ev.solana_specific is not None
    assert ev.solana_specific.block_time == 1_748_000_000
    jsonschema.validate(instance=ev.model_dump(mode="json"), schema=market_event_schema)


# ── (e) Round-trip: dump → re-parse → equal ────────────────────────────────────


def test_solana_order_roundtrips_through_json(order_schema):
    """ExecutionOrder.model_dump_json() must be parseable back into an equal
    ExecutionOrder, and the intermediate dict must satisfy the JSON Schema."""

    original = ExecutionOrder(
        order_id="ord-sol-rtrip",
        correlation_id="corr-sol-rt",
        timestamp=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        chain="solana",
        protocol="drift",
        action="open_perp",
        strategy="DRIFT-001:c-roundtrip",
        template_id="DRIFT-001",
        candidate_id="c-roundtrip",
        params=OrderParams(
            token_in="USDC",
            amount=Decimal("2500"),
            venue="drift",
            extra={"market_index": 0},
        ),
        limits=OrderLimits(
            max_priority_fee_lamports=Decimal("75000"),
            max_slippage_bps=75,
            deadline_unix=1_735_689_700,
        ),
        solana_specific=SolanaSpecificOrder(
            compute_unit_price=20_000,
            compute_unit_limit=400_000,
            lookup_tables=[
                "AddrLookupTblAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                "AddrLookupTblBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
            ],
        ),
    )

    wire = original.model_dump_json()
    reparsed = ExecutionOrder.model_validate_json(wire)
    assert reparsed == original
    assert reparsed.solana_specific is not None
    assert reparsed.solana_specific.lookup_tables == [
        "AddrLookupTblAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "AddrLookupTblBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    ]

    # The wire dict must also pass the JSON Schema (cross-language pin).
    jsonschema.validate(instance=json.loads(wire), schema=order_schema)
