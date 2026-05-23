"""Tests for oracle manipulation guard — RISK-007."""

from __future__ import annotations

from unittest.mock import MagicMock

from data.price_feed import PriceFeedManager, PriceResult
from risk.oracle_guard import OracleGuard


def _make_price_result(token: str, price: float, source: str = "alchemy") -> PriceResult:
    return PriceResult(
        token=token, price_usd=price, source=source, timestamp="2026-01-01T00:00:00Z",
    )


def _make_guard(*, deviation_threshold: float = 0.02, stale: bool = False) -> OracleGuard:
    """Create an OracleGuard with a mocked PriceFeedManager."""
    mock_redis = MagicMock()
    pf = PriceFeedManager(
        redis=mock_redis,
        deviation_threshold=deviation_threshold,
        fetch_fn=MagicMock(),
    )
    # Mock staleness check
    pf.is_any_stale = MagicMock(return_value=stale)
    return OracleGuard(pf, deviation_threshold=deviation_threshold)


# ---------------------------------------------------------------------------
# Cross-source deviation checks
# ---------------------------------------------------------------------------
class TestValidatePrices:

    def test_passes_when_prices_agree(self) -> None:
        guard = _make_guard()
        alchemy = {"USDC": _make_price_result("USDC", 1.0000)}
        defillama = {"USDC": _make_price_result("USDC", 1.0001, "defillama")}
        result = guard.validate_prices(alchemy, defillama)
        assert result.safe is True
        assert result.reason == "ok"

    def test_rejects_when_deviation_exceeds_threshold(self) -> None:
        guard = _make_guard()
        alchemy = {"USDC": _make_price_result("USDC", 1.00)}
        defillama = {"USDC": _make_price_result("USDC", 1.05, "defillama")}  # ~4.9%
        result = guard.validate_prices(alchemy, defillama)
        assert result.safe is False
        assert "USDC" in result.reason
        assert len(result.deviations) == 1
        assert result.deviations[0].exceeded is True

    def test_rejects_at_exactly_threshold(self) -> None:
        guard = _make_guard(deviation_threshold=0.02)
        # 2% deviation: prices 0.99 and 1.01 → mid=1.0, diff≈0.02
        # Float precision makes this slightly above threshold, so it's rejected
        alchemy = {"USDC": _make_price_result("USDC", 0.99)}
        defillama = {"USDC": _make_price_result("USDC", 1.01, "defillama")}
        result = guard.validate_prices(alchemy, defillama)
        assert result.safe is False

    def test_rejects_just_above_threshold(self) -> None:
        guard = _make_guard(deviation_threshold=0.02)
        # Just over 2%
        alchemy = {"USDC": _make_price_result("USDC", 0.989)}
        defillama = {"USDC": _make_price_result("USDC", 1.011, "defillama")}
        result = guard.validate_prices(alchemy, defillama)
        assert result.safe is False

    def test_multiple_tokens_one_exceeds(self) -> None:
        guard = _make_guard()
        alchemy = {
            "USDC": _make_price_result("USDC", 1.00),
            "DAI": _make_price_result("DAI", 1.00),
        }
        defillama = {
            "USDC": _make_price_result("USDC", 1.001, "defillama"),  # OK
            "DAI": _make_price_result("DAI", 1.05, "defillama"),      # >2%
        }
        result = guard.validate_prices(alchemy, defillama)
        assert result.safe is False
        assert "DAI" in result.reason
        assert len(result.deviations) == 2

    def test_all_tokens_within_threshold(self) -> None:
        guard = _make_guard()
        alchemy = {
            "USDC": _make_price_result("USDC", 1.0000),
            "USDT": _make_price_result("USDT", 1.0000),
            "DAI": _make_price_result("DAI", 0.9995),
        }
        defillama = {
            "USDC": _make_price_result("USDC", 1.0001, "defillama"),
            "USDT": _make_price_result("USDT", 0.9999, "defillama"),
            "DAI": _make_price_result("DAI", 1.0005, "defillama"),
        }
        result = guard.validate_prices(alchemy, defillama)
        assert result.safe is True
        assert len(result.deviations) == 3

    def test_no_common_tokens(self) -> None:
        guard = _make_guard()
        alchemy = {"USDC": _make_price_result("USDC", 1.00)}
        defillama = {"DAI": _make_price_result("DAI", 1.00, "defillama")}
        result = guard.validate_prices(alchemy, defillama)
        assert result.safe is True
        assert len(result.deviations) == 0

    def test_skips_zero_midpoint(self) -> None:
        guard = _make_guard()
        alchemy = {"USDC": _make_price_result("USDC", 0.0)}
        defillama = {"USDC": _make_price_result("USDC", 0.0, "defillama")}
        result = guard.validate_prices(alchemy, defillama)
        assert result.safe is True
        assert len(result.deviations) == 0


# ---------------------------------------------------------------------------
# Staleness integration
# ---------------------------------------------------------------------------
class TestStalenessIntegration:

    def test_stale_prices_mark_unsafe(self) -> None:
        guard = _make_guard(stale=True)
        alchemy = {"USDC": _make_price_result("USDC", 1.00)}
        defillama = {"USDC": _make_price_result("USDC", 1.00, "defillama")}
        result = guard.validate_prices(alchemy, defillama)
        assert result.safe is False
        assert result.stale is True
        assert "stale" in result.reason

    def test_not_stale_and_matching_is_safe(self) -> None:
        guard = _make_guard(stale=False)
        alchemy = {"USDC": _make_price_result("USDC", 1.00)}
        defillama = {"USDC": _make_price_result("USDC", 1.001, "defillama")}
        result = guard.validate_prices(alchemy, defillama)
        assert result.safe is True
        assert result.stale is False


# ---------------------------------------------------------------------------
# get_deviations audit logging
# ---------------------------------------------------------------------------
class TestGetDeviations:

    def test_returns_empty_before_validation(self) -> None:
        guard = _make_guard()
        assert guard.get_deviations() == {}

    def test_returns_deviations_after_validation(self) -> None:
        guard = _make_guard()
        alchemy = {
            "USDC": _make_price_result("USDC", 1.00),
            "DAI": _make_price_result("DAI", 1.00),
        }
        defillama = {
            "USDC": _make_price_result("USDC", 1.001, "defillama"),
            "DAI": _make_price_result("DAI", 1.03, "defillama"),
        }
        guard.validate_prices(alchemy, defillama)
        devs = guard.get_deviations()
        assert "USDC" in devs
        assert "DAI" in devs
        assert devs["DAI"] > devs["USDC"]

    def test_deviations_update_on_subsequent_calls(self) -> None:
        guard = _make_guard()
        # First call
        guard.validate_prices(
            {"USDC": _make_price_result("USDC", 1.00)},
            {"USDC": _make_price_result("USDC", 1.01, "defillama")},
        )
        first_dev = guard.get_deviations()["USDC"]
        # Second call with different prices
        guard.validate_prices(
            {"USDC": _make_price_result("USDC", 1.00)},
            {"USDC": _make_price_result("USDC", 1.005, "defillama")},
        )
        second_dev = guard.get_deviations()["USDC"]
        assert second_dev < first_dev


# ---------------------------------------------------------------------------
# check() convenience method
# ---------------------------------------------------------------------------
class TestCheckConvenience:

    def test_check_with_both_sources_available(self) -> None:
        guard = _make_guard()
        guard._price_feed._fetch_alchemy = MagicMock(
            return_value={"USDC": _make_price_result("USDC", 1.00)},
        )
        guard._price_feed._fetch_defillama = MagicMock(
            return_value={"USDC": _make_price_result("USDC", 1.001, "defillama")},
        )
        result = guard.check()
        assert result.safe is True

    def test_check_single_source_no_staleness(self) -> None:
        guard = _make_guard()
        guard._price_feed._fetch_alchemy = MagicMock(
            side_effect=Exception("API down"),
        )
        guard._price_feed._fetch_defillama = MagicMock(
            return_value={"USDC": _make_price_result("USDC", 1.00, "defillama")},
        )
        result = guard.check()
        assert result.safe is True
        assert "single source" in result.reason

    def test_check_single_source_with_staleness(self) -> None:
        guard = _make_guard(stale=True)
        guard._price_feed._fetch_alchemy = MagicMock(
            side_effect=Exception("API down"),
        )
        guard._price_feed._fetch_defillama = MagicMock(
            return_value={"USDC": _make_price_result("USDC", 1.00, "defillama")},
        )
        result = guard.check()
        assert result.safe is False
        assert result.stale is True


# ---------------------------------------------------------------------------
# Custom threshold
# ---------------------------------------------------------------------------
class TestCustomThreshold:

    def test_tighter_threshold_rejects_smaller_deviation(self) -> None:
        guard = _make_guard(deviation_threshold=0.005)  # 0.5%
        alchemy = {"USDC": _make_price_result("USDC", 1.00)}
        defillama = {"USDC": _make_price_result("USDC", 1.01, "defillama")}  # ~1%
        result = guard.validate_prices(alchemy, defillama)
        assert result.safe is False

    def test_looser_threshold_allows_larger_deviation(self) -> None:
        guard = _make_guard(deviation_threshold=0.05)  # 5%
        alchemy = {"USDC": _make_price_result("USDC", 1.00)}
        defillama = {"USDC": _make_price_result("USDC", 1.04, "defillama")}  # ~3.9%
        result = guard.validate_prices(alchemy, defillama)
        assert result.safe is True
