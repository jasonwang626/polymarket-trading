from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime
from typing import Protocol

from polymarket_agent.config import Settings
from polymarket_agent.data.storage import Storage
from polymarket_agent.features.market_features import FeatureEngine, rank_score
from polymarket_agent.models import (
    ExternalPrice,
    FeatureSnapshot,
    Market,
    OrderBook,
    ScanResult,
    ScanStatus,
    Trade,
)

LOGGER = logging.getLogger(__name__)


class DiscoverySource(Protocol):
    async def discover(self, now: datetime | None = None) -> list[Market]: ...

    async def close(self) -> None: ...


class MarketFeed(Protocol):
    async def get_market_books(self, market: Market) -> tuple[OrderBook, OrderBook]: ...

    async def get_recent_trades(self, market: Market) -> list[Trade]: ...

    async def close(self) -> None: ...


class PriceFeed(Protocol):
    async def get_btc_eth(self) -> dict[str, ExternalPrice]: ...

    async def close(self) -> None: ...


def classify(features: FeatureSnapshot, market: Market, settings: Settings) -> tuple[ScanStatus, list[str]]:
    reasons: list[str] = []
    if features.external_price_stale:
        return ScanStatus.NO_TRADE, ["外部價格缺失或過期"]
    if features.spread is None:
        return ScanStatus.NO_TRADE, ["YES order book 缺少雙邊報價"]
    if features.spread > settings.filters.max_spread:
        return ScanStatus.NO_TRADE, [f"spread {features.spread:.4f} 超過上限"]
    if features.seconds_to_expiry <= 0:
        return ScanStatus.NO_TRADE, ["市場已到期"]
    if market.liquidity < settings.filters.min_liquidity_usd:
        return ScanStatus.NO_TRADE, ["流動性低於下限"]

    yes_score = 0
    no_score = 0
    imbalance = features.orderbook_imbalance
    if imbalance is not None and imbalance >= settings.signals.min_orderbook_imbalance:
        yes_score += 1
        reasons.append(f"order book 偏向 YES ({imbalance:.2f})")
    elif imbalance is not None and imbalance <= settings.signals.max_orderbook_imbalance:
        no_score += 1
        reasons.append(f"order book 偏向 NO ({imbalance:.2f})")

    momentum = features.momentum_5m
    if momentum is not None and momentum >= settings.signals.min_momentum_5m:
        yes_score += 1
        reasons.append(f"YES 5m momentum 為正 ({momentum:+.2%})")
    elif momentum is not None and momentum <= -settings.signals.min_momentum_5m:
        no_score += 1
        reasons.append(f"YES 5m momentum 為負 ({momentum:+.2%})")

    external_return = features.external_return_5m
    if external_return is not None and external_return >= settings.signals.min_external_return_5m:
        yes_score += 1
        reasons.append(f"外部現貨 5m 上漲 ({external_return:+.2%})")
    elif external_return is not None and external_return <= -settings.signals.min_external_return_5m:
        no_score += 1
        reasons.append(f"外部現貨 5m 下跌 ({external_return:+.2%})")

    threshold = settings.signals.min_signal_score
    if yes_score >= threshold and yes_score > no_score:
        return ScanStatus.POSSIBLE_YES, reasons
    if no_score >= threshold and no_score > yes_score:
        return ScanStatus.POSSIBLE_NO, reasons
    reasons.append(f"訊號尚未達門檻（YES={yes_score}, NO={no_score}）")
    return ScanStatus.WATCH, reasons


class Scanner:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        discovery: DiscoverySource,
        market_feed: MarketFeed,
        price_feed: PriceFeed,
    ):
        self.settings = settings
        self.storage = storage
        self.discovery = discovery
        self.market_feed = market_feed
        self.price_feed = price_feed
        self.feature_engine = FeatureEngine(settings, storage)
        self._watchlist: list[Market] = []
        self._last_discovery: datetime | None = None
        self._semaphore = asyncio.Semaphore(settings.scanner.max_concurrency)

    async def close(self) -> None:
        await asyncio.gather(
            self.discovery.close(),
            self.market_feed.close(),
            self.price_feed.close(),
        )

    async def _refresh_watchlist(self, now: datetime) -> None:
        needs_refresh = (
            not self._watchlist
            or self._last_discovery is None
            or (now - self._last_discovery).total_seconds()
            >= self.settings.scanner.discovery_refresh_seconds
        )
        if needs_refresh:
            self._watchlist = await self.discovery.discover(now=now)
            self._last_discovery = now

    async def scan_once(self, now: datetime | None = None) -> list[ScanResult]:
        now = now or datetime.now(UTC)
        await self._refresh_watchlist(now)
        external_prices = await self.price_feed.get_btc_eth()
        for observation in external_prices.values():
            self.storage.insert_external_price(observation)

        tasks = [self._scan_market(market, external_prices, now) for market in self._watchlist]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[ScanResult] = []
        for market, item in zip(self._watchlist, raw_results, strict=True):
            if isinstance(item, Exception):
                LOGGER.warning(
                    "Market scan failed",
                    extra={"market_id": market.market_id, "error": repr(item)},
                )
            else:
                results.append(item)
        results.sort(key=lambda item: item.rank_score, reverse=True)
        LOGGER.info(
            "Scan cycle completed",
            extra={"market_count": len(results), "failed_count": len(raw_results) - len(results)},
        )
        return results

    async def _scan_market(
        self,
        market: Market,
        external_prices: dict[str, ExternalPrice],
        now: datetime,
    ) -> ScanResult:
        async with self._semaphore:
            yes_book, no_book = await self.market_feed.get_market_books(market)
            trades = await self.market_feed.get_recent_trades(market)

        self.storage.upsert_market(market, now)
        self.storage.insert_trades(trades)
        external = external_prices.get(market.underlying or "")
        snapshot, features = self.feature_engine.compute(
            market, yes_book, no_book, trades, external, now
        )
        status, reasons = classify(features, market, self.settings)
        score = rank_score(market, features)
        self.storage.insert_market_snapshot(snapshot)
        self.storage.insert_orderbook(market.market_id, "YES", yes_book)
        self.storage.insert_orderbook(market.market_id, "NO", no_book)
        self.storage.insert_features(features, status.value, score)
        return ScanResult(
            market=market,
            features=features,
            status=status,
            rank_score=score,
            reasons=reasons,
        )

    async def run_forever(self) -> None:
        while True:
            started = asyncio.get_running_loop().time()
            results = await self.scan_once()
            print_results(results)
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(self.settings.scanner.refresh_seconds - elapsed, 0))


def _fmt_money(value: float | None) -> str:
    return "—" if value is None else f"${value:,.0f}"


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2%}"


def _fmt_float(value: float | None, digits: int = 3) -> str:
    return "—" if value is None or not math.isfinite(value) else f"{value:.{digits}f}"


def print_results(results: list[ScanResult]) -> None:
    if not results:
        print("未找到符合條件且可完整讀取的加密貨幣市場。")
        return
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"\n=== Polymarket 唯讀掃描 {stamp} ===")
    for result in results:
        f = result.features
        print(f"\n{result.market.question}")
        print(f"YES       {_fmt_float(f.polymarket_yes_mid)}")
        print(f"NO        {_fmt_float(f.polymarket_no_mid)}")
        print(f"Spread    {_fmt_float(f.spread)}")
        print(f"Volume5m  {_fmt_money(f.volume_5m)}")
        print(f"VolAccel  {_fmt_float(f.volume_acceleration, 2)}x")
        print(f"OB Imbal  {_fmt_float(f.orderbook_imbalance, 2)}")
        print(f"{f.external_symbol or 'Spot':<9} {_fmt_money(f.external_spot)} / 5m {_fmt_pct(f.external_return_5m)}")
        print(f"Signal    {result.status.value}")
        print(f"Reason    {'；'.join(result.reasons)}")

