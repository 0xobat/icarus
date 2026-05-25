"""Rules-based `RegimeClassifier` implementation.

Computes the four sub-regimes (volatility, funding, trend, TVL) from a
single `MarketSnapshot` per the `RegimeClassifier` Protocol in
`icarus.protocols.regime`. Each sub-regime is bucketed from a continuous
feature so the allocator can branch on coarse labels while the LLM advisor
can still read the underlying numbers via `Regime.features`.

Sub-regime rules (blueprint Q2, line 365):

  - **Volatility** — annualised realised vol of log returns over the
    last 14 daily closes. Bucketed via two thresholds (defaults 30% / 80%
    annualised) into `low | normal | high`. Below 14 samples → `normal`
    with confidence penalty.

  - **Funding** — aggregate signed funding rate over all pools that
    publish one in `metadata["funding_rates"]`. Bucketed via two
    extremity thresholds (defaults ±10bp / ±50bp per 8h) into
    `neg_extreme | negative | neutral | positive | pos_extreme`. No
    funding pools published → `neutral` with confidence penalty.

  - **Trend** — normalised linear-regression slope of price over the
    last 30 daily closes, expressed in standard-deviations per day
    (slope ÷ stddev(prices)). |slope_norm| < 0.02 → `mean_reverting`;
    slope_norm > 0 → `trending_up`; slope_norm < 0 → `trending_down`.
    Below 14 samples → `mean_reverting` with confidence penalty (safe
    no-trade default).

  - **TVL** — 24h delta of the largest pool's TVL in
    `metadata["tvl_history"]`. > +1% → `growing`; < -1% → `declining`;
    else → `stable`. No TVL history → `stable` with confidence penalty.

`Regime.confidence` is the product of per-sub-regime confidences (each in
[0, 1]); confidence == 0 means "no signal" and the allocator should fall
back to cold-start behaviour. `source = "rules_v1"` distinguishes this
classifier from the LLM advisor in disagreement-analysis dashboards.

Pure compute. No I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np

from icarus.protocols.regime import (
    FundingRegime,
    Regime,
    TrendRegime,
    TvlRegime,
    VolRegime,
)
from icarus.types import MarketSnapshot

# Daily-bar conventions; pinned here so unit tests can reason about them.
_ANNUALISATION_DAYS = 365
_VOL_WINDOW_DAYS = 14
_TREND_WINDOW_DAYS = 30
_MIN_HISTORY_FOR_SIGNAL = 14

# Bucket thresholds — chosen to match blueprint "low / normal / high" intent
# on majors (BTC, ETH typically sit in 40-80% annualised vol).
_VOL_LOW_MAX = 0.30  # annualised stddev below this → "low"
_VOL_HIGH_MIN = 0.80  # annualised stddev above this → "high"

# Funding-rate thresholds in per-8h fractional terms. Synthetix/Hyperliquid
# style: 10bp = "extended"; 50bp = "extreme".
_FUNDING_NEUTRAL_BAND = Decimal("0.0001")  # |r| < 10bp → neutral
_FUNDING_EXTREME = Decimal("0.0050")  # |r| > 50bp → extreme

# Trend slope expressed in stddev-of-price-units per day. 2% of one stddev
# per day → "trending"; below → "mean_reverting".
_TREND_FLAT_BAND = 0.02

# TVL bucketing on 24h percentage delta.
_TVL_STABLE_BAND = Decimal("0.01")  # ±1%


@dataclass(frozen=True, slots=True)
class RegimeFeatures:
    """Intermediate numerical features computed during classification.

    Mirrors `Regime.features` but typed as native Python floats / Decimals
    for in-process consumers (webapp insight panel, LLM advisor prompt
    builder, disagreement-analysis dashboards). The `Mapping[str, Decimal]`
    on `Regime` is the cross-process wire shape; `RegimeFeatures` is the
    in-process view.
    """

    realised_vol_annualised: float
    aggregate_funding_rate: Decimal
    trend_slope_normalised: float
    tvl_delta_24h: Decimal
    samples_used_vol: int
    samples_used_trend: int
    funding_pools_seen: int
    tvl_pools_seen: int

    def as_decimal_mapping(self) -> dict[str, Decimal]:
        """Project into the `Mapping[str, Decimal]` shape `Regime.features` expects."""
        return {
            "realised_vol_annualised": Decimal(str(self.realised_vol_annualised)),
            "aggregate_funding_rate": self.aggregate_funding_rate,
            "trend_slope_normalised": Decimal(str(self.trend_slope_normalised)),
            "tvl_delta_24h": self.tvl_delta_24h,
            "samples_used_vol": Decimal(self.samples_used_vol),
            "samples_used_trend": Decimal(self.samples_used_trend),
            "funding_pools_seen": Decimal(self.funding_pools_seen),
            "tvl_pools_seen": Decimal(self.tvl_pools_seen),
        }


@dataclass(frozen=True, slots=True)
class RulesRegimeClassifier:
    """Deterministic four-sub-regime classifier (blueprint Q2 primary).

    Construct with the asset whose price history drives the volatility and
    trend sub-regimes (typically the operator's risk asset — "ETH" on Base,
    "SOL" on Solana). Funding and TVL sub-regimes aggregate across every
    pool that publishes the relevant metadata, so they are asset-agnostic.

    Satisfies `icarus.protocols.regime.RegimeClassifier` via duck-typed
    `name: str` attribute + `classify(market) -> Regime` method.
    """

    asset: str = "ETH"
    name: str = field(default="rules_v1")

    def classify(self, market: MarketSnapshot) -> Regime:
        """Return the four-sub-regime `Regime` for `market`. Pure, deterministic."""
        prices = _extract_price_history(market.metadata, self.asset)
        funding = _extract_funding_rates(market.metadata)
        tvl_series = _extract_tvl_history(market.metadata)

        vol_label, vol_value, vol_n, vol_conf = _classify_volatility(prices)
        trend_label, trend_value, trend_n, trend_conf = _classify_trend(prices)
        funding_label, funding_value, funding_n, funding_conf = _classify_funding(funding)
        tvl_label, tvl_value, tvl_n, tvl_conf = _classify_tvl(tvl_series)

        features = RegimeFeatures(
            realised_vol_annualised=vol_value,
            aggregate_funding_rate=funding_value,
            trend_slope_normalised=trend_value,
            tvl_delta_24h=tvl_value,
            samples_used_vol=vol_n,
            samples_used_trend=trend_n,
            funding_pools_seen=funding_n,
            tvl_pools_seen=tvl_n,
        )

        # Mean of the four sub-confidences — a missing axis (e.g. no
        # funding history published) drags confidence down proportionally
        # but does not zero out the regime. Confidence == 0 means *every*
        # axis lacked the data it needed, which is the cross-Protocol
        # equivalent of the W6 brief's "regime_break" state and the signal
        # the allocator reads to fall back to cold-start equal-weight.
        confidence = Decimal(
            str(round((vol_conf + trend_conf + funding_conf + tvl_conf) / 4.0, 6))
        )

        return Regime(
            volatility=vol_label,
            funding=funding_label,
            trend=trend_label,
            tvl=tvl_label,
            confidence=confidence,
            features=features.as_decimal_mapping(),
            source=self.name,
            rationale="",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Metadata extraction helpers
# ──────────────────────────────────────────────────────────────────────────────


def _extract_price_history(metadata: Any, asset: str) -> np.ndarray:
    raw = _safe_mapping(metadata.get("price_history") if hasattr(metadata, "get") else None)
    series = raw.get(asset) if raw else None
    if not isinstance(series, Sequence) or isinstance(series, str | bytes):
        return np.empty(0, dtype=np.float64)
    try:
        return np.asarray([float(Decimal(str(p))) for p in series], dtype=np.float64)
    except (ValueError, ArithmeticError):
        return np.empty(0, dtype=np.float64)


def _extract_funding_rates(metadata: Any) -> dict[str, Decimal]:
    raw = _safe_mapping(metadata.get("funding_rates") if hasattr(metadata, "get") else None)
    out: dict[str, Decimal] = {}
    for pool_id, rate in (raw or {}).items():
        try:
            out[str(pool_id)] = Decimal(str(rate))
        except (ValueError, ArithmeticError):
            continue
    return out


def _extract_tvl_history(metadata: Any) -> dict[str, list[Decimal]]:
    raw = _safe_mapping(metadata.get("tvl_history") if hasattr(metadata, "get") else None)
    out: dict[str, list[Decimal]] = {}
    for pool_id, series in (raw or {}).items():
        if not isinstance(series, Sequence) or isinstance(series, str | bytes):
            continue
        try:
            out[str(pool_id)] = [Decimal(str(v)) for v in series]
        except (ValueError, ArithmeticError):
            continue
    return out


def _safe_mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "items"):
        try:
            return {str(k): v for k, v in value.items()}
        except (TypeError, ValueError):
            return None
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Sub-regime classifiers — each returns (label, raw_feature_value, sample_count, confidence)
# ──────────────────────────────────────────────────────────────────────────────


def _classify_volatility(prices: np.ndarray) -> tuple[VolRegime, float, int, float]:
    if prices.size < _MIN_HISTORY_FOR_SIGNAL:
        return "normal", 0.0, int(prices.size), 0.0
    window = prices[-_VOL_WINDOW_DAYS:]
    # Guard against zero / negative prices that would break log returns.
    if np.any(window <= 0):
        return "normal", 0.0, int(window.size), 0.0
    log_returns = np.diff(np.log(window))
    if log_returns.size < 2:
        return "normal", 0.0, int(window.size), 0.0
    daily_std = float(np.std(log_returns, ddof=1))
    annualised = daily_std * float(np.sqrt(_ANNUALISATION_DAYS))
    if annualised < _VOL_LOW_MAX:
        label: VolRegime = "low"
    elif annualised > _VOL_HIGH_MIN:
        label = "high"
    else:
        label = "normal"
    return label, annualised, int(window.size), 1.0


def _classify_trend(prices: np.ndarray) -> tuple[TrendRegime, float, int, float]:
    if prices.size < _MIN_HISTORY_FOR_SIGNAL:
        return "mean_reverting", 0.0, int(prices.size), 0.0
    window = prices[-_TREND_WINDOW_DAYS:]
    if np.any(window <= 0):
        return "mean_reverting", 0.0, int(window.size), 0.0
    n = window.size
    x = np.arange(n, dtype=np.float64)
    # Centre x and y so slope = cov(x,y)/var(x) without an intercept term.
    x_centred = x - x.mean()
    y_centred = window - window.mean()
    denom = float(np.dot(x_centred, x_centred))
    if denom == 0.0:
        return "mean_reverting", 0.0, int(n), 1.0
    slope = float(np.dot(x_centred, y_centred) / denom)
    price_std = float(np.std(window, ddof=1))
    if price_std == 0.0:
        # Perfectly flat prices — unambiguous mean-reversion.
        return "mean_reverting", 0.0, int(n), 1.0
    slope_norm = slope / price_std
    confidence = 1.0 if n >= _TREND_WINDOW_DAYS else float(n) / float(_TREND_WINDOW_DAYS)
    if abs(slope_norm) < _TREND_FLAT_BAND:
        label: TrendRegime = "mean_reverting"
    elif slope_norm > 0:
        label = "trending_up"
    else:
        label = "trending_down"
    return label, slope_norm, int(n), confidence


def _classify_funding(
    funding: dict[str, Decimal],
) -> tuple[FundingRegime, Decimal, int, float]:
    if not funding:
        return "neutral", Decimal("0"), 0, 0.0
    # Equal-weight aggregate — pools are already on the same per-8h scale.
    total = sum(funding.values(), Decimal("0"))
    avg = total / Decimal(len(funding))
    if avg <= -_FUNDING_EXTREME:
        label: FundingRegime = "neg_extreme"
    elif avg <= -_FUNDING_NEUTRAL_BAND:
        label = "negative"
    elif avg >= _FUNDING_EXTREME:
        label = "pos_extreme"
    elif avg >= _FUNDING_NEUTRAL_BAND:
        label = "positive"
    else:
        label = "neutral"
    return label, avg, len(funding), 1.0


def _classify_tvl(
    tvl_series: dict[str, list[Decimal]],
) -> tuple[TvlRegime, Decimal, int, float]:
    # Pick the largest pool by latest TVL — that's the one whose
    # trajectory dominates aggregate protocol health.
    candidates = [(pool_id, series) for pool_id, series in tvl_series.items() if len(series) >= 2]
    if not candidates:
        return "stable", Decimal("0"), 0, 0.0
    pool_id, series = max(candidates, key=lambda item: item[1][-1])
    prev, latest = series[-2], series[-1]
    if prev == 0:
        return "stable", Decimal("0"), len(candidates), 0.0
    delta = (latest - prev) / prev
    if delta >= _TVL_STABLE_BAND:
        label: TvlRegime = "growing"
    elif delta <= -_TVL_STABLE_BAND:
        label = "declining"
    else:
        label = "stable"
    # Anchor pool_id usage so the linter stays happy + record-keeping is honest.
    _ = pool_id
    return label, delta, len(candidates), 1.0
