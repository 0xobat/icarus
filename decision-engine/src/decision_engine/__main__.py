"""decision-engine entrypoint — managed-portfolio runtime cycle.

One tick (`ManagedEngine._tick`):
  1. Fetch a MarketSnapshot (real ETH price + gas) via RpcAdapter.
  2. Feed the gas-spike breaker (current + EMA average) and the drawdown
     breaker (live NAV) — closes the "breakers never fed" gap.
  3. Read on-chain holdings; run one ManagedPortfolioCycle tick (plan →
     resolve → risk gate → publish).
A background task consumes execution:results:{chain} → tx-failure monitor.

The lake-era discovery machinery (extractor/backtest workers, lake-governor,
the `DecisionCycle`/roster path) is parked, not wired here — it remains in the
tree (and is exercised by `harness/e2e_smoke.py`) per the "discovery deferred,
not deleted" pivot. The managed-portfolio cleanup removed only the genuinely
unused v4.2 risk breakers (exposure/position-loss/tvl/oracle/post-action advisor).
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from datetime import UTC, datetime
from decimal import Decimal

import redis.asyncio as redis
import structlog
from icarus.data_adapters import RpcAdapter
from icarus.db.database import DatabaseConfig, DatabaseManager
from web3 import AsyncHTTPProvider, AsyncWeb3, Web3

from decision_engine.config import load_managed_config
from decision_engine.cycle import RedisExecutorPublisher
from decision_engine.gas_tracker import GasAverageTracker
from decision_engine.health_monitor import PortfolioHealthMonitor
from decision_engine.holdings import RpcHoldingsProvider
from decision_engine.managed_cycle import ManagedPortfolioCycle
from decision_engine.order_resolver import lookup_token, register_token
from decision_engine.pnl import ContributedCapitalTracker
from decision_engine.results_consumer import ResultsConsumer
from decision_engine.risk.depeg_breaker import DepegBreaker
from decision_engine.risk.drawdown_breaker import DrawdownBreaker
from decision_engine.risk.gas_spike_breaker import GasSpikeBreaker
from decision_engine.risk.managed_exposure import (
    ManagedExposureChecker,
    ManagedExposureConfig,
)
from decision_engine.risk.tx_failure_monitor import TxFailureMonitor
from decision_engine.risk_gate import (
    DepegChecker,
    DrawdownChecker,
    GasSpikeChecker,
    RiskGate,
    TxFailureChecker,
)
from decision_engine.trade_log import make_db_trade_sink, record_pending_trade

logger = structlog.get_logger(service="decision-engine")


class ManagedEngine:
    """Managed-portfolio worker. Owns the tick loop + results-consumer task."""

    def __init__(
        self,
        *,
        cycle,
        holdings,
        adapter,
        drawdown: DrawdownBreaker,
        gas_spike: GasSpikeBreaker,
        gas_tracker: GasAverageTracker,
        chain: str,
        depeg: DepegBreaker | None = None,
        interval_seconds: int = 3600,
        consumer: ResultsConsumer | None = None,
        redis_client=None,
        pending_trade_recorder=None,
        pnl_tracker=None,
        pnl_refresh_every: int = 24,
        health_monitor=None,
        venue_by_asset: dict[str, str] | None = None,
    ) -> None:
        self._cycle = cycle
        self._holdings = holdings
        self._adapter = adapter
        self._drawdown = drawdown
        self._gas_spike = gas_spike
        self._depeg = depeg
        self._gas_tracker = gas_tracker
        self._chain = chain
        self._interval = interval_seconds
        self._consumer = consumer
        self._redis = redis_client
        self._pending_trade_recorder = pending_trade_recorder
        # PnL deposit-tracker (reporting only, best-effort). Refreshed on a slow
        # cadence (deposits change rarely); contributed_usd is read each tick.
        self._pnl_tracker = pnl_tracker
        self._pnl_refresh_every = max(1, pnl_refresh_every)
        self._health_monitor = health_monitor
        self._venue_by_asset = venue_by_asset or {}
        self._tick_count = 0
        self._stop = asyncio.Event()

    @staticmethod
    def _compute_pnl(nav_usd: Decimal, contributed_usd: Decimal) -> tuple[Decimal, Decimal]:
        """PnL in USD and percent of contributed capital.

        pnl_usd = nav - contributed; pnl_pct = pnl_usd / contributed * 100
        (0 when contributed is 0, avoiding division by zero).
        """
        pnl_usd = nav_usd - contributed_usd
        pnl_pct = (
            (pnl_usd / contributed_usd * Decimal(100))
            if contributed_usd != 0
            else Decimal(0)
        )
        return pnl_usd, pnl_pct

    async def _record_portfolio_pnl(self, nav_usd: Decimal) -> None:
        """Best-effort PnL surfacing. Refreshes contributed capital on a slow
        cadence and logs a `portfolio_pnl` event. A REPORTING concern: any
        failure logs and returns — it never touches the cycle or the breakers.
        """
        if self._pnl_tracker is None:
            return
        try:
            if (self._tick_count - 1) % self._pnl_refresh_every == 0:
                await self._pnl_tracker.refresh()
            contributed = self._pnl_tracker.contributed_usd
            if contributed is None:
                logger.info("portfolio_pnl_unavailable", nav_usd=str(nav_usd))
                return
            pnl_usd, pnl_pct = self._compute_pnl(nav_usd, contributed)
            logger.info(
                "portfolio_pnl",
                nav_usd=str(nav_usd),
                contributed_usd=str(contributed),
                pnl_usd=str(pnl_usd),
                pnl_pct=str(pnl_pct),
            )
        except Exception:
            logger.warning("portfolio_pnl_failed", exc_info=True)

    def _surface_health(self, holdings) -> None:
        """Best-effort: surface de-risk signals from the health monitor.

        A REPORTING/safety-surface concern — any failure logs and returns, never
        touching the cycle or breakers. The USDC depeg halt is enforced at the
        gate; this makes the de-risk decision visible (and is where urgent
        de-risk-order emission will hook in at the fill run)."""
        if self._health_monitor is None:
            return
        try:
            actions = self._health_monitor.assess(
                holdings=holdings, venue_by_asset=self._venue_by_asset,
            )
            for action in actions:
                logger.warning(
                    "derisk_signal", kind=action.kind, asset=action.asset, reason=action.reason,
                )
        except Exception:
            logger.warning("health_monitor_failed", exc_info=True)

    async def _tick(self) -> None:
        self._tick_count += 1
        market = await self._adapter.fetch_live(self._chain)
        avg = self._gas_tracker.update(market.gas_gwei)
        self._gas_spike.update(market.gas_gwei, avg)
        # Feed the depeg breaker only when the snapshot carries a live USDC price
        # (i.e. a USDC feed is configured). No feed → no update → never trips,
        # which is today's $1-pinned behaviour.
        if self._depeg is not None:
            usdc_price = market.prices.get("USDC")
            if usdc_price is not None:
                self._depeg.update(usdc_price)
        holdings = await self._holdings.current_usd_by_asset()
        nav_usd = sum(holdings.values(), Decimal("0"))
        self._drawdown.update(nav_usd)
        self._surface_health(holdings)
        result = await self._cycle.run_one()
        if result.published and self._pending_trade_recorder is not None:
            try:
                self._pending_trade_recorder(result)
            except Exception:
                logger.warning(
                    "pending_trade_record_failed", order_id=result.order_id, exc_info=True
                )
        logger.info(
            "managed_tick",
            action=result.action,
            reason=result.reason,
            published=result.published,
            nav_usd=str(nav_usd),
            gas_gwei=str(market.gas_gwei),
        )
        # Reporting only — surfaced AFTER the cycle, fully isolated from it.
        await self._record_portfolio_pnl(nav_usd)

    async def run(self) -> None:
        logger.info("managed_engine_start", interval_s=self._interval, chain=self._chain)
        consumer_task = None
        pubsub = None
        if self._consumer is not None and self._redis is not None:
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(f"execution:results:{self._chain}")
            consumer_task = asyncio.create_task(self._consumer.run(pubsub))
            consumer_task.add_done_callback(self._on_consumer_done)
        try:
            while not self._stop.is_set():
                try:
                    await self._tick()
                except Exception:
                    logger.exception("managed_tick_failed")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                except TimeoutError:
                    continue
        finally:
            if consumer_task is not None:
                consumer_task.cancel()
            if pubsub is not None:
                await pubsub.aclose()
            logger.info("managed_engine_stop")

    def _on_consumer_done(self, task: asyncio.Task) -> None:
        """Fail-closed: if the results consumer dies (not a clean cancel), the
        tx-failure breaker is no longer fed — halt trading and alert loudly."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("consumer_task_died", error=str(exc), exc_info=exc)
            self.request_stop()

    def request_stop(self) -> None:
        self._stop.set()


def _build_risk_gate(*, drawdown, gas_spike, tx_failure, depeg, exposure) -> RiskGate:
    """The managed gate: NAV/market-level breakers + the P2.5 exposure cap.

    The lake-era ExposureChecker + PositionLossChecker were removed (the
    per-protocol exposure cap was a category error and the per-strategy loss
    cooldown would freeze all rebalancing — every order is REBAL:base). The P2.5
    `ManagedExposureChecker` is the replacement: a position-aware per-asset cap
    (a safety net above the allocation band) + an overlay per-venue cap, fed real
    per-tick holdings via RiskContext. See the P2.5 plan + the right-sizing plan.

    The DepegChecker (capital protection) halts all rebalancing while USDC is
    off-peg; ordered among the cheap state-reads, after GasSpike. The exposure
    cap runs last (it consults the prospective holdings the cycle threaded in).
    """
    return RiskGate(
        [
            DrawdownChecker(drawdown),
            TxFailureChecker(tx_failure),
            GasSpikeChecker(gas_spike),
            DepegChecker(depeg),
            exposure,
        ]
    )


def _apply_token_overrides(chain_id: int, env: dict[str, str]) -> None:
    """Override token addresses for the active network from env, if provided.

    Lets an operator point USDC/WETH/cbBTC at specific testnet contracts without
    a code change: set USDC_ADDRESS / WETH_ADDRESS / CBBTC_ADDRESS (+ optional
    *_DECIMALS). cbBTC has no canonical Base-Sepolia deploy, so a testnet run
    that includes the wBTC sleeve must supply CBBTC_ADDRESS.
    """
    for symbol, dec_default in (("USDC", 6), ("WETH", 18), ("cbBTC", 8), ("wstETH", 18)):
        env_key = symbol.upper()  # cbBTC → CBBTC_ADDRESS; env vars are uppercase
        addr = env.get(f"{env_key}_ADDRESS")
        if addr:
            decimals = int(env.get(f"{env_key}_DECIMALS", str(dec_default)))
            register_token(chain_id=chain_id, symbol=symbol, address=addr, decimals=decimals)
            logger.info("token_override", symbol=symbol, address=addr, chain_id=chain_id)


def _make_pending_recorder(db, *, chain: str, protocol: str, slippage_bps: int):
    def _record(result) -> None:
        record_pending_trade(
            db, order_id=result.order_id, correlation_id=result.correlation_id,
            chain=chain, protocol=protocol, from_symbol=result.from_symbol,
            to_symbol=result.to_symbol, usd_amount=result.usd_amount,
            slippage_bps=slippage_bps,
        )

    return _record


async def _amain() -> int:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )
    logger.info("decision_engine_init", started_at=datetime.now(UTC).isoformat())

    config = load_managed_config(
        os.environ.get("MANAGED_CONFIG_PATH", "/app/config/managed.toml")
    )
    _apply_token_overrides(config.chain_id, dict(os.environ))

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    redis_client = redis.from_url(redis_url, decode_responses=True)

    db = DatabaseManager(DatabaseConfig())
    db.create_tables()

    # Managed risk gate: NAV/market-level breakers (drawdown, gas-spike,
    # tx-failure, USDC depeg). The lake-era exposure limiter and position-loss
    # cooldown are not wired here — the allocation target + bands are the
    # concentration policy.
    drawdown = DrawdownBreaker()
    gas_spike = GasSpikeBreaker()
    tx_failure = TxFailureMonitor()
    depeg = DepegBreaker(threshold_bps=config.depeg_threshold_bps)
    # P2.5/P3.3 exposure cap: per-asset (safety net) + per-venue. The LP overlay
    # venue is capped at its tighter lp_cap (0.15); core Aave lending of the
    # allocation assets is governed by the bands, not capped.
    lp_venue = "aerodrome_lp"
    exposure = ManagedExposureChecker(
        ManagedExposureConfig(
            max_asset_pct=config.max_asset_pct,
            max_venue_pct=config.max_venue_pct,
            venue_caps={lp_venue: config.lp_cap},
        ),
        capped_venues=frozenset({lp_venue}),
    )
    risk_gate = _build_risk_gate(
        drawdown=drawdown, gas_spike=gas_spike, tx_failure=tx_failure, depeg=depeg,
        exposure=exposure,
    )

    # One shared AsyncWeb3 feeds the adapter (prices/gas) and holdings (balances).
    rpc_url = os.environ["ALCHEMY_BASE_HTTP_URL"]
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    # The Chainlink ETH/USD feed is network-specific. RpcAdapter defaults to the
    # Base MAINNET feed; on a testnet (e.g. Base Sepolia) that address reverts, so
    # let the operator point it at the right feed via env. Unset → mainnet default.
    adapter_kwargs: dict[str, str] = {}
    eth_usd_feed = os.environ.get("CHAINLINK_ETH_USD_ADDRESS")
    if eth_usd_feed:
        adapter_kwargs["chainlink_eth_usd_address"] = Web3.to_checksum_address(eth_usd_feed)
        logger.info("chainlink_feed_override", address=adapter_kwargs["chainlink_eth_usd_address"])
    # Optional USDC/USD peg feed. Unset → USDC stays $1 and the depeg breaker
    # never trips (backward compatible). Set → USDC priced live + breaker armed.
    usdc_usd_feed = os.environ.get("CHAINLINK_USDC_USD_ADDRESS")
    if usdc_usd_feed:
        adapter_kwargs["chainlink_usdc_usd_address"] = Web3.to_checksum_address(usdc_usd_feed)
        logger.info(
            "chainlink_usdc_feed_set", address=adapter_kwargs["chainlink_usdc_usd_address"]
        )
    else:
        logger.warning(
            "chainlink_usdc_feed_not_configured",
            note=(
                "USDC pinned to $1; depeg breaker UNARMED — "
                "set CHAINLINK_USDC_USD_ADDRESS to arm it"
            ),
        )
    adapter = RpcAdapter(w3=w3, **adapter_kwargs)
    holdings = RpcHoldingsProvider(
        w3=w3, adapter=adapter, safe_address=config.safe_address,
        symbols=list(config.weights), chain=config.chain, chain_id=config.chain_id,
    )
    cycle = ManagedPortfolioCycle(
        adapter=adapter, holdings=holdings, target=config.multi_asset_target(),
        risk_gate=risk_gate, publisher=RedisExecutorPublisher(redis_client),
        config=config.cycle_config(),
    )
    # PnL deposit-tracker (reporting only, best-effort). Built only when operator
    # funding addresses are configured; otherwise PnL tracking is disabled and we
    # warn at boot (the engine then runs + trades exactly as before).
    pnl_tracker = None
    if config.operator_funding_addresses:
        usdc_info = lookup_token(config.chain, "USDC", chain_id=config.chain_id)
        weth_info = lookup_token(config.chain, "WETH", chain_id=config.chain_id)
        pnl_tracker = ContributedCapitalTracker(
            w3=w3,
            safe_address=config.safe_address,
            funding_addresses=config.operator_funding_addresses,
            eth_price_at_block=adapter.eth_price_at_block,
            usdc_address=usdc_info.address,
            weth_address=weth_info.address,
        )
        logger.info(
            "pnl_tracker_enabled",
            funding_addresses=sorted(config.operator_funding_addresses),
        )
    else:
        logger.warning(
            "pnl_disabled",
            note=(
                "OPERATOR_FUNDING_ADDRESSES unset → contributed-capital/PnL "
                "tracking disabled (reporting only; trading unaffected)"
            ),
        )

    trade_sink = make_db_trade_sink(db)
    consumer = ResultsConsumer(tx_failure=tx_failure, trade_sink=trade_sink)
    pending_recorder = _make_pending_recorder(
        db, chain=config.chain, protocol="aerodrome", slippage_bps=config.slippage_bps
    )

    # P3.2 portfolio health monitor: surfaces de-risk signals (USDC depeg →
    # halt_all; LST depeg / unhealthy venue → exit_position). LST breakers join
    # the map when the wstETH sleeve goes live (needs the LST market price +
    # rate in the snapshot); empty today → it surfaces the USDC depeg.
    health_monitor = PortfolioHealthMonitor(usdc_depeg=depeg, lst_breakers={})

    engine = ManagedEngine(
        cycle=cycle, holdings=holdings, adapter=adapter, drawdown=drawdown,
        gas_spike=gas_spike, gas_tracker=GasAverageTracker(), chain=config.chain,
        depeg=depeg, interval_seconds=config.interval_seconds, consumer=consumer,
        redis_client=redis_client, pending_trade_recorder=pending_recorder,
        pnl_tracker=pnl_tracker, health_monitor=health_monitor,
        venue_by_asset=config.venue_by_asset(),
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, engine.request_stop)

    try:
        await engine.run()
    finally:
        await redis_client.aclose()
        db.close()
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
