"""PnL deposit-tracker — contributed capital from net external deposits.

Managed-portfolio PnL v1. A REPORTING feature, NOT capital protection: it is
entirely best-effort and read-only. A failure here (Alchemy error, missing
config, an unsupported method on the active network) logs and continues — it
can NEVER alter or halt a trading decision. Isolated from the gate and the
cycle exactly like `trade_log` (the engine wraps the per-tick call in
try/except and the breaker logic never reads this value).

What it computes:
    contributed_usd = sum(deposit_amt * price@block) - sum(withdrawal_amt * price@block)

  - A *deposit* is an inbound transfer to the Safe FROM a configured operator
    funding address; a *withdrawal* is an outbound transfer from the Safe TO
    such an address. Everything else — DEX swap proceeds, the Safe's own
    WETH-wrap, faucets — is ignored. This cleanly excludes internal flows.
  - USDC is priced at $1 at deposit time; ETH/WETH at the Chainlink ETH/USD
    price at the deposit block (`RpcAdapter.eth_price_at_block`).

Then PnL = current NAV - contributed_usd, surfaced (logged) by the engine.

Data source: Alchemy `alchemy_getAssetTransfers` via the AsyncWeb3 provider
(`w3.provider.make_request(...)`). Categories ["external", "erc20"] for native
ETH + ERC-20 (USDC/WETH). We read `rawContract.value` (hex smallest-units) +
`decimals` for precision and `blockNum` (hex) for the block. If the method is
unsupported on the active network, we degrade gracefully (log + leave the
cache unchanged, never raise to the engine).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import structlog

logger = structlog.get_logger(service="decision-engine.pnl")

# Alchemy transfer categories that carry the deposits we care about: native ETH
# ("external") and ERC-20 (USDC/WETH). "internal" (contract-internal value
# moves) is intentionally excluded — operator deposits are top-level transfers.
_TRANSFER_CATEGORIES = ["external", "erc20"]

# Page size cap for alchemy_getAssetTransfers (hex). 0x3e8 = 1000, the max.
_MAX_COUNT_HEX = "0x3e8"

# Pagination safety cap. alchemy_getAssetTransfers returns up to 1000 rows per
# page plus a `pageKey` when more exist; we page until it's absent, but bound
# the loop so a pathological/looping response can't spin forever. 50 pages =
# up to 50k transfers per direction — far beyond any real managed Safe.
_MAX_PAGES = 50

Direction = Literal["in", "out"]

# An async "price ETH/USD at this block" callable (RpcAdapter.eth_price_at_block).
EthPriceAtBlock = Callable[[int], Awaitable[Decimal]]


@dataclass(frozen=True)
class Transfer:
    """One classified, operator-funding transfer to/from the Safe.

    `amount` is in whole tokens (already scaled by decimals). `direction` is
    "in" (a deposit into the Safe) or "out" (a withdrawal from it).
    """

    asset: str
    amount: Decimal
    block_no: int
    direction: Direction
    counterparty: str


async def net_contributed_usd(
    transfers: list[Transfer],
    *,
    eth_price_at_block: EthPriceAtBlock,
) -> Decimal:
    """Net contributed capital in USD: deposits add, withdrawals subtract.

    Pricing: USDC → $1; ETH/WETH → the ETH/USD price at the transfer's block.
    Pure Decimal arithmetic (no float). An empty list nets to 0.
    """
    total = Decimal(0)
    for t in transfers:
        if t.asset == "USDC":
            usd = t.amount  # $1 per USDC
        else:  # ETH / WETH
            price = await eth_price_at_block(t.block_no)
            usd = t.amount * price
        if t.direction == "in":
            total += usd
        else:
            total -= usd
    return total


def _to_int(hex_or_int: Any) -> int:
    """Parse a 0x-hex string (or int) → int. Alchemy returns hex strings."""
    if isinstance(hex_or_int, int):
        return hex_or_int
    return int(hex_or_int, 16)


class ContributedCapitalTracker:
    """Fetches + caches contributed capital from operator deposits/withdrawals.

    Best-effort and read-only. `refresh()` calls Alchemy twice (inbound to the
    Safe, outbound from it), keeps only transfers whose counterparty is a
    configured funding address, classifies them in/out, and recomputes
    `contributed_usd`. Any failure logs and leaves the cached value unchanged —
    it never raises to the caller.

    `contributed_usd` is None until the first successful refresh.
    """

    def __init__(
        self,
        *,
        w3: Any,
        safe_address: str,
        funding_addresses: frozenset[str],
        eth_price_at_block: EthPriceAtBlock,
        usdc_address: str,
        weth_address: str,
    ) -> None:
        self._w3 = w3
        self._safe = safe_address
        # Compare on lowercase: Alchemy echoes addresses lowercased, while the
        # configured funding set is checksummed.
        self._funding = frozenset(a.lower() for a in funding_addresses)
        self._eth_price_at_block = eth_price_at_block
        self._usdc = usdc_address.lower()
        self._weth = weth_address.lower()
        self._contributed_usd: Decimal | None = None

    @property
    def contributed_usd(self) -> Decimal | None:
        """Cached contributed capital in USD; None until first good refresh."""
        return self._contributed_usd

    async def _get_transfers(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Page alchemy_getAssetTransfers and return all `transfers` rows.

        Alchemy caps a single response at `maxCount` (1000) rows and returns a
        `pageKey` in `result` when more exist. We loop, passing the previous
        `pageKey` back in, until none is returned — otherwise a long-running
        Safe would silently drop transfers and skew PnL. Bounded by `_MAX_PAGES`
        so a misbehaving endpoint can't loop forever; hitting the cap logs a
        warning so the truncation is at least observable.

        Raises on an error-shaped JSON-RPC reply so `refresh` can degrade.
        """
        rows: list[dict[str, Any]] = []
        page_key: str | None = None
        for _page in range(_MAX_PAGES):
            page_params = dict(params)
            if page_key is not None:
                page_params["pageKey"] = page_key
            resp = await self._w3.provider.make_request(
                "alchemy_getAssetTransfers", [page_params]
            )
            if not isinstance(resp, dict) or resp.get("error") is not None:
                raise RuntimeError(f"alchemy_getAssetTransfers error: {resp!r}")
            result = resp.get("result") or {}
            rows.extend(result.get("transfers", []))
            page_key = result.get("pageKey")
            if not page_key:
                return rows
        # Exhausted the page budget with more pages still pending → observable
        # truncation rather than a silent drop.
        logger.warning(
            "contributed_capital_pagination_capped",
            max_pages=_MAX_PAGES,
            rows_collected=len(rows),
        )
        return rows

    def _classify(self, row: dict[str, Any], direction: Direction) -> Transfer | None:
        """Build a Transfer if the row is an operator deposit/withdrawal, else None.

        For an inbound row the counterparty is `from`; for outbound, `to`. We
        keep the row only when that counterparty is a configured funding
        address, and only for assets we can price (ETH/WETH/USDC).
        """
        counterparty = (row.get("from") if direction == "in" else row.get("to")) or ""
        counterparty = counterparty.lower()
        if counterparty not in self._funding:
            return None

        asset = self._normalize_asset(row)
        if asset is None:
            return None

        raw = row.get("rawContract") or {}
        value_hex = raw.get("value")
        if value_hex is None:
            return None
        decimals = _to_int(raw.get("decimals", "0x12"))  # default 18
        amount = Decimal(_to_int(value_hex)) / (Decimal(10) ** decimals)
        # Bare key access on a malformed row would KeyError and abort the whole
        # refresh — skip just this row instead.
        block_hex = row.get("blockNum")
        if block_hex is None:
            return None
        block_no = _to_int(block_hex)
        return Transfer(
            asset=asset, amount=amount, block_no=block_no,
            direction=direction, counterparty=counterparty,
        )

    def _classify_rows(
        self, rows: list[dict[str, Any]], direction: Direction
    ) -> list[Transfer]:
        """Classify a page of rows, skipping (not failing on) any malformed one.

        A single unparseable row must not abort the whole refresh, so each
        `_classify` call is wrapped — a bad row is logged and dropped.
        """
        out: list[Transfer] = []
        for row in rows:
            try:
                t = self._classify(row, direction)
            except Exception:
                logger.warning(
                    "contributed_capital_row_skipped", direction=direction, exc_info=True
                )
                continue
            if t is not None:
                out.append(t)
        return out

    def _normalize_asset(self, row: dict[str, Any]) -> str | None:
        """Map a transfer row to one of ETH / WETH / USDC, or None if unpriceable.

        Native ETH transfers (category "external") carry no contract address;
        ERC-20 rows carry `rawContract.address`. We resolve by address for
        ERC-20s and by the symbol for native ETH.
        """
        raw = row.get("rawContract") or {}
        contract_addr = (raw.get("address") or "").lower()
        if contract_addr == self._usdc:
            return "USDC"
        if contract_addr == self._weth:
            return "WETH"
        if not contract_addr:
            # No contract → native ETH (Alchemy reports asset="ETH").
            if (row.get("asset") or "").upper() == "ETH":
                return "ETH"
        return None

    async def refresh(self) -> None:
        """Recompute + cache contributed_usd. Best-effort: never raises.

        On any failure we log and leave the cache unchanged (prior value or
        None) — the engine treats a None/stale value as "PnL unavailable".
        """
        try:
            inbound_params = {
                "fromBlock": "0x0",
                "toBlock": "latest",
                "toAddress": self._safe,
                "category": _TRANSFER_CATEGORIES,
                "withMetadata": False,
                "excludeZeroValue": True,
                "maxCount": _MAX_COUNT_HEX,
            }
            outbound_params = {**inbound_params}
            del outbound_params["toAddress"]
            outbound_params["fromAddress"] = self._safe

            inbound_rows = await self._get_transfers(inbound_params)
            outbound_rows = await self._get_transfers(outbound_params)

            transfers: list[Transfer] = []
            transfers.extend(self._classify_rows(inbound_rows, "in"))
            transfers.extend(self._classify_rows(outbound_rows, "out"))

            contributed = await net_contributed_usd(
                transfers, eth_price_at_block=self._eth_price_at_block
            )
            self._contributed_usd = contributed
            logger.info(
                "contributed_capital_refresh",
                contributed_usd=str(contributed),
                deposits=sum(1 for t in transfers if t.direction == "in"),
                withdrawals=sum(1 for t in transfers if t.direction == "out"),
            )
        except Exception:
            # REPORTING feature: degrade gracefully, never raise to the engine.
            logger.warning("contributed_capital_refresh_failed", exc_info=True)


__all__ = ["ContributedCapitalTracker", "Transfer", "net_contributed_usd"]
