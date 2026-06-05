"""decision-engine entrypoint — managed-portfolio runtime cycle.

One tick (`ManagedEngine._tick`):
  1. Fetch a MarketSnapshot (real ETH price + gas) via RpcAdapter.
  2. Feed the gas-spike breaker (current + EMA average) and the drawdown
     breaker (live NAV) — closes the "breakers never fed" gap.
  3. Read on-chain holdings; run one ManagedPortfolioCycle tick (plan →
     resolve → risk gate → publish).
A background task consumes execution:results:{chain} → tx-failure monitor.

The lake modules (DecisionCycle/roster/registry/allocator) remain in the tree
but are no longer wired here; a cleanup phase removes them after the managed
path is proven live.
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
from decision_engine.holdings import RpcHoldingsProvider
from decision_engine.managed_cycle import ManagedPortfolioCycle
from decision_engine.order_resolver import register_token
from decision_engine.results_consumer import ResultsConsumer
from decision_engine.risk.drawdown_breaker import DrawdownBreaker
from decision_engine.risk.exposure_limits import ExposureLimiter
from decision_engine.risk.gas_spike_breaker import GasSpikeBreaker
from decision_engine.risk.position_loss_limit import PositionLossLimit
from decision_engine.risk.tx_failure_monitor import TxFailureMonitor
from decision_engine.risk_gate import (
    DrawdownChecker,
    ExposureChecker,
    GasSpikeChecker,
    PositionLossChecker,
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
        interval_seconds: int = 3600,
        consumer: ResultsConsumer | None = None,
        redis_client=None,
        pending_trade_recorder=None,
    ) -> None:
        self._cycle = cycle
        self._holdings = holdings
        self._adapter = adapter
        self._drawdown = drawdown
        self._gas_spike = gas_spike
        self._gas_tracker = gas_tracker
        self._chain = chain
        self._interval = interval_seconds
        self._consumer = consumer
        self._redis = redis_client
        self._pending_trade_recorder = pending_trade_recorder
        self._stop = asyncio.Event()

    async def _tick(self) -> None:
        market = await self._adapter.fetch_live(self._chain)
        avg = self._gas_tracker.update(market.gas_gwei)
        self._gas_spike.update(market.gas_gwei, avg)
        crypto_usd, stable_usd = await self._holdings.current_usd_holdings()
        self._drawdown.update(crypto_usd + stable_usd)
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
            nav_usd=str(crypto_usd + stable_usd),
            gas_gwei=str(market.gas_gwei),
        )

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


def _build_risk_gate(*, drawdown, exposure, gas_spike, position_loss, tx_failure) -> RiskGate:
    return RiskGate(
        [
            DrawdownChecker(drawdown),
            TxFailureChecker(tx_failure),
            GasSpikeChecker(gas_spike),
            ExposureChecker(exposure),
            PositionLossChecker(position_loss),
        ]
    )


def _apply_token_overrides(chain_id: int, env: dict[str, str]) -> None:
    """Override token addresses for the active network from env, if provided.

    Lets an operator point USDC/WETH at specific testnet contracts without a
    code change: set USDC_ADDRESS / WETH_ADDRESS (+ optional *_DECIMALS).
    """
    for symbol, dec_default in (("USDC", 6), ("WETH", 18)):
        addr = env.get(f"{symbol}_ADDRESS")
        if addr:
            decimals = int(env.get(f"{symbol}_DECIMALS", str(dec_default)))
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

    # Risk modules (kept from the lake wiring) + the capital fail-loud guard.
    drawdown = DrawdownBreaker()
    total_capital_raw = os.environ.get("DECISION_ENGINE_TOTAL_CAPITAL_USD")
    if not total_capital_raw:
        raise RuntimeError(
            "DECISION_ENGINE_TOTAL_CAPITAL_USD is required — the exposure limiter "
            "divides by it. Set it in .env to the live NAV ceiling."
        )
    exposure = ExposureLimiter(total_capital=Decimal(total_capital_raw))
    gas_spike = GasSpikeBreaker()
    position_loss = PositionLossLimit()
    tx_failure = TxFailureMonitor()
    risk_gate = _build_risk_gate(
        drawdown=drawdown, exposure=exposure, gas_spike=gas_spike,
        position_loss=position_loss, tx_failure=tx_failure,
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
    adapter = RpcAdapter(w3=w3, **adapter_kwargs)
    holdings = RpcHoldingsProvider(
        w3=w3, adapter=adapter, safe_address=config.safe_address,
        crypto_symbol=config.crypto_symbol, stable_symbol=config.stable_symbol,
        chain=config.chain, chain_id=config.chain_id,
    )
    cycle = ManagedPortfolioCycle(
        adapter=adapter, holdings=holdings, target=config.rebalance_target(),
        risk_gate=risk_gate, publisher=RedisExecutorPublisher(redis_client),
        config=config.cycle_config(),
    )
    trade_sink = make_db_trade_sink(db)
    consumer = ResultsConsumer(tx_failure=tx_failure, trade_sink=trade_sink)
    pending_recorder = _make_pending_recorder(
        db, chain=config.chain, protocol="aerodrome", slippage_bps=config.slippage_bps
    )

    engine = ManagedEngine(
        cycle=cycle, holdings=holdings, adapter=adapter, drawdown=drawdown,
        gas_spike=gas_spike, gas_tracker=GasAverageTracker(), chain=config.chain,
        interval_seconds=config.interval_seconds, consumer=consumer,
        redis_client=redis_client, pending_trade_recorder=pending_recorder,
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
