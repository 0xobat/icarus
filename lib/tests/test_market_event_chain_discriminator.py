"""MarketEvent's pydantic validator must enforce the same chain↔chain_specific
invariant as the JSON Schema's allOf/if-then in shared/schemas/market-event.schema.json.

Without this test, the Python producer side could silently drift from the
TypeScript consumer side: pydantic would accept envelopes (both chain_specific
fields optional) that the JSON Schema would reject. Two representations of
the same contract — pin both to the same rule.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from icarus.envelopes import (
    BaseChainSpecific,
    MarketEvent,
    SolanaChainSpecific,
)
from pydantic import ValidationError


def _base_specific() -> BaseChainSpecific:
    return BaseChainSpecific(block_number=20_000_000, tx_hash="0xabc")


def _solana_specific() -> SolanaChainSpecific:
    return SolanaChainSpecific(slot=265_000_000)


def _kwargs_common() -> dict:
    return dict(
        timestamp=datetime.now(UTC),
        sequence=1,
        event_type="swap",
        protocol="aerodrome",
        correlation_id="c1",
    )


# --- Acceptance: matched pair on each chain ---


def test_base_event_with_base_specific_accepted() -> None:
    ev = MarketEvent(chain="base", base_specific=_base_specific(), **_kwargs_common())
    assert ev.chain == "base"
    assert ev.base_specific is not None
    assert ev.solana_specific is None


def test_solana_event_with_solana_specific_accepted() -> None:
    ev = MarketEvent(chain="solana", solana_specific=_solana_specific(), **_kwargs_common())
    assert ev.chain == "solana"
    assert ev.solana_specific is not None
    assert ev.base_specific is None


# --- Rejection: missing required chain_specific ---


def test_base_event_without_base_specific_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        MarketEvent(chain="base", **_kwargs_common())
    assert "base_specific" in str(exc.value)


def test_solana_event_without_solana_specific_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        MarketEvent(chain="solana", **_kwargs_common())
    assert "solana_specific" in str(exc.value)


# --- Rejection: wrong chain_specific present ---


def test_base_event_with_solana_specific_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        MarketEvent(
            chain="base",
            base_specific=_base_specific(),
            solana_specific=_solana_specific(),
            **_kwargs_common(),
        )
    assert "solana_specific" in str(exc.value)


def test_solana_event_with_base_specific_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        MarketEvent(
            chain="solana",
            base_specific=_base_specific(),
            solana_specific=_solana_specific(),
            **_kwargs_common(),
        )
    assert "base_specific" in str(exc.value)


# --- Round-trip with the JSON Schema (pinning the cross-language contract) ---


def test_validator_matches_json_schema_when_both_present() -> None:
    """Confirm that a pydantic-rejected envelope is also JSON-Schema-rejected."""
    import json
    from pathlib import Path

    import jsonschema

    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "shared" / "schemas" / "market-event.schema.json"
        ).read_text()
    )

    # Construct a valid envelope, then mutate it post-hoc to violate the
    # invariant and confirm JSON Schema rejects too. (pydantic refuses to
    # build the violating envelope, so we build the dict manually.)
    valid = MarketEvent(chain="base", base_specific=_base_specific(), **_kwargs_common())
    doc = valid.model_dump(mode="json")
    # Now mutate to break the invariant.
    doc["solana_specific"] = {"slot": 1, "signature": None, "priority_fee_lamports": None}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=doc, schema=schema)
