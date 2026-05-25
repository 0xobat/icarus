"""Unit tests for `RulesRegimeClassifier`.

Covers the five behaviours the blueprint Q2 contract pins down:

  (a) constant prices → mean-reverting trend, low realised vol
  (b) monotonic price increase → trending_up
  (c) high-vol noise around a constant → high vol + mean-reverting trend
  (d) insufficient history (< 14 daily closes) → safe neutral default,
      `confidence == 0` (the cross-Protocol equivalent of the "regime_break"
      label in the W6 brief — the `Regime` literal has no such state, so the
      classifier signals "no signal" via `confidence == 0` instead, which
      the allocator already interprets as cold-start equal-weight)
  (e) instance satisfies the `RegimeClassifier` Protocol at runtime

Seeded synthetic data via `np.random.default_rng(seed=42)` per the W6 brief.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pytest
from icarus.protocols import RegimeClassifier
from icarus.regime import RegimeFeatures, RulesRegimeClassifier
from icarus.types import MarketSnapshot


def _snapshot(price_history: list[float], asset: str = "ETH") -> MarketSnapshot:
    """Build a MarketSnapshot whose metadata carries `price_history[asset]`."""
    return MarketSnapshot(
        timestamp=datetime(2026, 5, 25, tzinfo=UTC),
        chain="base",
        prices={asset: Decimal(str(price_history[-1])) if price_history else Decimal("0")},
        apys={},
        pool_state={},
        gas_gwei=Decimal("0.05"),
        metadata={
            "price_history": {asset: [Decimal(str(p)) for p in price_history]},
        },
    )


def test_a_constant_prices_yield_low_vol_mean_reverting() -> None:
    # 30 days of perfectly flat $3500 ETH — no trend, no vol.
    classifier = RulesRegimeClassifier(asset="ETH")
    regime = classifier.classify(_snapshot([3500.0] * 30))

    assert regime.trend == "mean_reverting"
    assert regime.volatility == "low"
    assert regime.source == "rules_v1"
    assert regime.features["realised_vol_annualised"] == Decimal("0")
    assert regime.features["trend_slope_normalised"] == Decimal("0")


def test_b_monotonic_increase_yields_trending_up() -> None:
    # 30 days, $3000 → $3580 in $20/day steps — unambiguous upward trend.
    classifier = RulesRegimeClassifier(asset="ETH")
    prices = [3000.0 + 20.0 * i for i in range(30)]
    regime = classifier.classify(_snapshot(prices))

    assert regime.trend == "trending_up"
    # Confidence must be strictly positive when we have full-window data.
    assert regime.confidence > Decimal("0")
    assert regime.features["trend_slope_normalised"] > Decimal("0")


def test_c_high_vol_noise_around_constant_yields_high_vol_mean_reverting() -> None:
    # 30 days of large symmetric noise around $3500 — high realised vol,
    # no net drift, so vol=high + trend=mean_reverting.
    rng = np.random.default_rng(seed=42)
    # ±15% daily shocks comfortably push annualised stddev above the 80% bucket.
    shocks = rng.normal(loc=0.0, scale=0.15, size=30)
    prices = [3500.0 * float(np.exp(s)) for s in shocks]
    classifier = RulesRegimeClassifier(asset="ETH")
    regime = classifier.classify(_snapshot(prices))

    assert regime.volatility == "high"
    assert regime.trend == "mean_reverting"
    assert regime.features["realised_vol_annualised"] > Decimal("0.80")


def test_d_insufficient_history_yields_safe_neutral_with_zero_confidence() -> None:
    # 5 days of history is below the 14-day minimum — classifier must
    # NOT invent a regime; it returns the safe-default bucket on every
    # axis and signals "no signal" via confidence == 0.
    classifier = RulesRegimeClassifier(asset="ETH")
    regime = classifier.classify(_snapshot([3500.0, 3510.0, 3520.0, 3530.0, 3540.0]))

    assert regime.volatility == "normal"
    assert regime.trend == "mean_reverting"
    assert regime.funding == "neutral"
    assert regime.tvl == "stable"
    assert regime.confidence == Decimal("0")
    assert regime.features["samples_used_vol"] == Decimal("5")


def test_d2_empty_metadata_is_handled_without_raising() -> None:
    # Defensive: a snapshot with no price_history at all must still
    # return a Regime, not raise.
    classifier = RulesRegimeClassifier(asset="ETH")
    bare = MarketSnapshot(
        timestamp=datetime(2026, 5, 25, tzinfo=UTC),
        chain="base",
        prices={"ETH": Decimal("3500")},
        apys={},
        pool_state={},
        gas_gwei=Decimal("0.05"),
        metadata={},
    )
    regime = classifier.classify(bare)
    assert regime.confidence == Decimal("0")
    assert regime.source == "rules_v1"


def test_e_classifier_satisfies_protocol_at_runtime() -> None:
    # `RegimeClassifier` is `@runtime_checkable`; an `isinstance` check
    # is the contract test the W5 drift bug should have had.
    classifier = RulesRegimeClassifier(asset="ETH")
    assert isinstance(classifier, RegimeClassifier)


def test_funding_aggregate_buckets_into_extreme_when_above_50bp() -> None:
    # Two pools, average funding 60bp/8h → pos_extreme. Confirms the
    # asset-agnostic funding sub-regime kicks in even without price history.
    classifier = RulesRegimeClassifier(asset="ETH")
    snapshot = MarketSnapshot(
        timestamp=datetime(2026, 5, 25, tzinfo=UTC),
        chain="base",
        prices={"ETH": Decimal("3500")},
        apys={},
        pool_state={},
        gas_gwei=Decimal("0.05"),
        metadata={
            "funding_rates": {
                "synthetix_perps_base:eth-usd": Decimal("0.0070"),
                "hyperliquid:btc-usd": Decimal("0.0050"),
            },
        },
    )
    regime = classifier.classify(snapshot)
    assert regime.funding == "pos_extreme"


def test_tvl_growth_detected_from_24h_delta() -> None:
    # Two-point TVL series, 5% growth → "growing".
    classifier = RulesRegimeClassifier(asset="ETH")
    snapshot = MarketSnapshot(
        timestamp=datetime(2026, 5, 25, tzinfo=UTC),
        chain="base",
        prices={"ETH": Decimal("3500")},
        apys={},
        pool_state={},
        gas_gwei=Decimal("0.05"),
        metadata={
            "tvl_history": {
                "aave_v3_base:usdc": [Decimal("1000000"), Decimal("1050000")],
            },
        },
    )
    regime = classifier.classify(snapshot)
    assert regime.tvl == "growing"


def test_regime_features_is_frozen() -> None:
    # `RegimeFeatures` is the in-process view of `Regime.features`; immutable
    # so downstream readers (webapp, LLM advisor) can cache without defensive copy.
    f = RegimeFeatures(
        realised_vol_annualised=0.5,
        aggregate_funding_rate=Decimal("0"),
        trend_slope_normalised=0.0,
        tvl_delta_24h=Decimal("0"),
        samples_used_vol=14,
        samples_used_trend=30,
        funding_pools_seen=0,
        tvl_pools_seen=0,
    )
    with pytest.raises((AttributeError, TypeError)):
        f.realised_vol_annualised = 0.9  # type: ignore[misc]
