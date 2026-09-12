from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx

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


@dataclass(frozen=True)
class RunSummary:
    started_at: datetime
    ended_at: datetime
    attempted_cycles: int
    successful_cycles: int
    failed_cycles: int
    result_count: int
    market_failures: int
    stop_reason: str


class DiscoverySource(Protocol):
    async def discover(self, now: datetime | None = None) -> list[Market]: ...

    async def close(self) -> None: ...


class MarketFeed(Protocol):
    async def get_market_books(self, market: Market) -> tuple[OrderBook, OrderBook]: ...

    async def get_recent_trades(self, market: Market) -> list[Trade] | None: ...

    async def close(self) -> None: ...


class PriceFeed(Protocol):
    async def get_btc_eth(self) -> dict[str, ExternalPrice]: ...

    async def close(self) -> None: ...


def classify(features: FeatureSnapshot, market: Market, settings: Settings) -> tuple[ScanStatus, list[str]]:
    reasons: list[str] = []
    if features.data_issues:
        return ScanStatus.NO_TRADE, features.data_issues
    if market.rule_issues:
        return ScanStatus.NO_TRADE, market.rule_issues
    if not market.active or market.closed or not market.accepting_orders:
        return ScanStatus.NO_TRADE, ["市場未開放"]
    if features.external_price_stale:
        return ScanStatus.NO_TRADE, ["外部價格缺失或過期"]
    if features.spread is None:
        return ScanStatus.NO_TRADE, ["YES order book 缺少雙邊報價"]
    if features.spread < 0:
        return ScanStatus.NO_TRADE, ["買賣價交叉"]
    if features.spread > settings.filters.max_spread:
        return ScanStatus.NO_TRADE, [f"spread {features.spread:.4f} 超過上限"]
    if features.seconds_to_expiry <= 0:
        return ScanStatus.NO_TRADE, ["市場已到期"]
    if market.venue == "polymarket_us":
        if market.contract_type == "unknown":
            return ScanStatus.NO_TRADE, ["尚未支援的合約規則"]
        if min(features.bid_depth_usd, features.ask_depth_usd) < settings.filters.min_book_depth_usd:
            return ScanStatus.NO_TRADE, [
                ("最優價附近的雙邊掛單金額不足"
                f"（買方 ${features.bid_depth_usd:.2f}；賣方 ${features.ask_depth_usd:.2f}；"
                f"每側門檻 ${settings.filters.min_book_depth_usd:.2f}）")
            ]
        return ScanStatus.WATCH, [
            f"US {market.contract_type} 行情監控；尚未建立經驗證的勝率模型",
            "公開 REST 未提供逐筆成交流；成交量特徵保留空值",
            "外部現貨僅作參考，合約依 CF Benchmarks BRTI 規則判定",
        ]
    if market.liquidity < settings.filters.min_liquidity_usd:
        return ScanStatus.NO_TRADE, ["流動性低於下限"]
    if not features.recent_trades_available or features.recent_trade_count == 0:
        return ScanStatus.NO_TRADE, ["缺少近期有效成交"]

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
    if external_return is not None and (
        market.contract_type.endswith("_below")
        or re.search(r"\b(below|under)\b", market.question, re.IGNORECASE)
    ):
        external_return = -external_return
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
        # Monotonic deadlines only affect live reads; explicit historical clocks
        # must not inherit a live polling schedule.
        self._halted_until: dict[str, float] = {}
        self._last_discovery: datetime | None = None
        self._last_market_failures = 0
        self._semaphore = asyncio.Semaphore(settings.scanner.max_concurrency)

    async def close(self) -> None:
        await asyncio.gather(
            self.discovery.close(),
            self.market_feed.close(),
            self.price_feed.close(),
        )

    async def _refresh_watchlist(self, now: datetime) -> None:
        needs_refresh = (
            self._last_discovery is None
            or (now - self._last_discovery).total_seconds()
            >= self.settings.scanner.discovery_refresh_seconds
        )
        if needs_refresh:
            try:
                self._watchlist = await self.discovery.discover(now=now)
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                self._watchlist = []
                self._last_discovery = None
                raise
            self._last_discovery = now
            active_ids = {market.market_id for market in self._watchlist}
            self._halted_until = {key: value for key, value in self._halted_until.items()
                                  if key in active_ids}

    async def scan_once(self, now: datetime | None = None) -> list[ScanResult]:
        await self._refresh_watchlist(now or datetime.now(UTC))
        external_prices = await self.price_feed.get_btc_eth()
        for observation in external_prices.values():
            self.storage.insert_external_price(observation)

        tasks = [self._scan_market(market, external_prices, now) for market in self._watchlist]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        self._last_market_failures = sum(isinstance(item, Exception) for item in raw_results)
        results: list[ScanResult] = []
        for market, item in zip(self._watchlist, raw_results, strict=True):
            if isinstance(item, Exception):
                LOGGER.warning(
                    "Market scan failed",
                    extra={"market_id": market.market_id, "error": repr(item)},
                )
                stamp = now or datetime.now(UTC)
                seconds = max((market.resolution_time - stamp).total_seconds(), 0)
                features = FeatureSnapshot(
                    market_id=market.market_id, timestamp=stamp,
                    seconds_to_expiry=seconds, minutes_to_expiry=seconds / 60,
                    data_issues=["行情讀取失敗，本輪不產生訊號"],
                )
                self.storage.upsert_market(market, stamp)
                self.storage.insert_features(
                    features,
                    ScanStatus.NO_TRADE.value,
                    -1_000_000,
                    features.data_issues,
                )
                results.append(ScanResult(
                    market=market, features=features, status=ScanStatus.NO_TRADE,
                    rank_score=-1_000_000, reasons=features.data_issues,
                ))
            else:
                results.append(item)
        results.sort(key=lambda item: item.rank_score, reverse=True)
        LOGGER.info(
            "Scan cycle completed",
            extra={"market_count": len(results),
                   "failed_count": self._last_market_failures},
        )
        return results

    async def _scan_market(
        self,
        market: Market,
        external_prices: dict[str, ExternalPrice],
        now: datetime | None,
    ) -> ScanResult:
        loop = asyncio.get_running_loop()
        if now is None and market.market_id in self._halted_until:
            remaining = self._halted_until[market.market_id] - loop.time()
            if remaining > 0:
                stamp = datetime.now(UTC)
                seconds = max((market.resolution_time - stamp).total_seconds(), 0)
                reason = f"上次確認市場暫停；本輪未重新讀取，約 {remaining:.0f} 秒後檢查"
                features = FeatureSnapshot(
                    market_id=market.market_id, timestamp=stamp,
                    seconds_to_expiry=seconds, minutes_to_expiry=seconds / 60,
                    data_issues=[reason],
                )
                self.storage.upsert_market(market, stamp)
                self.storage.insert_features(
                    features,
                    ScanStatus.NO_TRADE.value,
                    -1_000_000,
                    [reason],
                )
                LOGGER.info("Halted market polling deferred market=%s remaining_seconds=%.3f",
                            market.market_id, remaining)
                return ScanResult(market=market, features=features, status=ScanStatus.NO_TRADE,
                                  rank_score=-1_000_000, reasons=[reason])
            # Failed rechecks retain the cooldown, preventing repeated requests
            # every cycle when a previously halted market's endpoint fails.
            self._halted_until[market.market_id] = (
                loop.time() + self.settings.scanner.halted_recheck_seconds
            )
        async with self._semaphore:
            yes_book, no_book = await self.market_feed.get_market_books(market)
            trades = await self.market_feed.get_recent_trades(market)
        if now is None:
            if yes_book.state == "HALTED" or no_book.state == "HALTED":
                self._halted_until[market.market_id] = (
                    loop.time() + self.settings.scanner.halted_recheck_seconds
                )
            else:
                self._halted_until.pop(market.market_id, None)

        # Live observations arrive after the cycle starts. Historical callers may
        # supply a fixed as-of clock; they must never ingest observations after it.
        now = now or datetime.now(UTC)
        self.storage.upsert_market(market, now)
        self.storage.insert_trades([t for t in trades or [] if t.timestamp <= now])
        external = external_prices.get(market.underlying or "")
        snapshot, features = self.feature_engine.compute(
            market, yes_book, no_book, trades, external, now
        )
        status, reasons = classify(features, market, self.settings)
        score = rank_score(market, features)
        self.storage.insert_market_snapshot(snapshot)
        self.storage.insert_orderbook(market.market_id, "YES", yes_book)
        self.storage.insert_orderbook(market.market_id, "NO", no_book)
        self.storage.insert_features(features, status.value, score, reasons)
        return ScanResult(
            market=market,
            features=features,
            status=status,
            rank_score=score,
            reasons=reasons,
        )

    async def run_forever(
        self,
        max_cycles: int | None = None,
        max_duration_seconds: float | None = None,
        show_results: bool = True,
    ) -> RunSummary:
        if max_cycles is not None and max_cycles < 1:
            raise ValueError("max_cycles must be positive")
        if max_duration_seconds is not None and max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds must be positive")
        loop = asyncio.get_running_loop()
        started_at = datetime.now(UTC)
        deadline = loop.time() + max_duration_seconds if max_duration_seconds is not None else None
        attempted = successful = failed = result_count = market_failures = 0
        stop_reason = "max_cycles"
        while max_cycles is None or attempted < max_cycles:
            if deadline is not None and loop.time() >= deadline:
                stop_reason = "duration"
                break
            cycle_started = loop.time()
            attempted += 1
            try:
                results = await self.scan_once()
                if show_results:
                    print_results(results)
                successful += 1
                result_count += len(results)
                market_failures += self._last_market_failures
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                failed += 1
                LOGGER.warning("Scan cycle failed; retrying on next cycle",
                               extra={"error": repr(exc)})
            if max_cycles is not None and attempted >= max_cycles:
                stop_reason = "max_cycles"
                break
            elapsed = loop.time() - cycle_started
            sleep_seconds = max(self.settings.scanner.refresh_seconds - elapsed, 0)
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    stop_reason = "duration"
                    break
                sleep_seconds = min(sleep_seconds, remaining)
            await asyncio.sleep(sleep_seconds)
        return RunSummary(
            started_at=started_at,
            ended_at=datetime.now(UTC),
            attempted_cycles=attempted,
            successful_cycles=successful,
            failed_cycles=failed,
            result_count=result_count,
            market_failures=market_failures,
            stop_reason=stop_reason,
        )


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
    print(f"\n=== Polymarket US 唯讀掃描 {stamp} ===")
    for result in results:
        f = result.features
        print(f"\n{result.market.question}")
        print(f"Deadline  {result.market.resolution_time.isoformat()} / {result.market.contract_type}")
        print(f"YES       {_fmt_float(f.polymarket_yes_mid)}")
        print(f"NO        {_fmt_float(f.polymarket_no_mid)}")
        print(f"Spread    {_fmt_float(f.spread)}")
        print(f"Volume5m  {_fmt_money(f.volume_5m)}")
        print(f"VolAccel  {_fmt_float(f.volume_acceleration, 2)}x")
        if f.polymarket_yes_mid is None:
            print("Depth USD —（本輪無有效雙邊報價）")
        else:
            print(f"Depth USD 買方 ${f.bid_depth_usd:,.2f} / 賣方 ${f.ask_depth_usd:,.2f}")
        print(f"OB Imbal  {_fmt_float(f.orderbook_imbalance, 2)}")
        print(f"{f.external_symbol or 'Spot':<9} {_fmt_money(f.external_spot)} / 5m {_fmt_pct(f.external_return_5m)}")
        print(f"Signal    {result.status.value}")
        print("Fair odds 尚未建模")
        print(f"Reason    {'；'.join(result.reasons)}")
