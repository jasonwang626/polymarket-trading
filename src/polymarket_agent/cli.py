from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from polymarket_agent.config import assert_no_trading_secrets, load_settings
from polymarket_agent.data.crypto_feed import CryptoPriceFeed
from polymarket_agent.data.polymarket_us import PolymarketUSFeed
from polymarket_agent.data.public_http import PublicAPI
from polymarket_agent.data.storage import Storage
from polymarket_agent.discovery.polymarket_us import PolymarketUSDiscovery
from polymarket_agent.logging import configure_logging
from polymarket_agent.offline import OfflineDiscovery, OfflineMarketFeed, OfflinePriceFeed
from polymarket_agent.scanner.scanner import Scanner, print_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket US 中長天期 BTC 唯讀掃描器")
    parser.add_argument("--config", type=Path, help="settings.yaml 路徑")
    parser.add_argument("--once", action="store_true", help="只掃描一輪後結束")
    parser.add_argument("--offline", action="store_true", help="使用內建 fixtures，不呼叫網路")
    parser.add_argument("--cycles", type=int, help="執行指定輪數後停止")
    parser.add_argument("--database", type=Path, help="指定驗證用 SQLite，與既有資料分開")
    parser.add_argument("--max-markets", type=int, help="限制本次監控合約數")
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    assert_no_trading_secrets()
    settings = load_settings(args.config)
    if args.cycles is not None and args.cycles < 1:
        raise ValueError("--cycles must be positive")
    if args.database:
        settings.storage.sqlite_path = args.database.resolve()
    if args.offline and not args.database:
        settings.storage.sqlite_path = settings.project_root / "data/offline-us.sqlite3"
    if args.max_markets is not None:
        if not 1 <= args.max_markets <= 200:
            raise ValueError("--max-markets must be 1..200")
        settings.scanner.max_watchlist_size = args.max_markets
    configure_logging(settings.app.log_level, settings.app.log_path)
    storage = Storage(settings.storage.sqlite_path)
    storage.initialize()
    logging.getLogger(__name__).info(
        "Starting read-only scanner",
        extra={"read_only": settings.app.read_only, "offline": args.offline},
    )

    public_api = None
    if args.offline:
        fixture_dir = settings.project_root / "tests/fixtures"
        discovery = OfflineDiscovery(fixture_dir)
        market_feed = OfflineMarketFeed(fixture_dir)
        price_feed = OfflinePriceFeed()
    else:
        public_api = PublicAPI(settings)
        discovery = PolymarketUSDiscovery(settings, public_api)
        market_feed = PolymarketUSFeed(settings, public_api)
        price_feed = CryptoPriceFeed(settings)

    scanner = Scanner(settings, storage, discovery, market_feed, price_feed)
    try:
        if args.once:
            print_results(await scanner.scan_once())
        else:
            await scanner.run_forever(max_cycles=args.cycles)
    finally:
        await scanner.close()
        if public_api is not None:
            await public_api.close()
    return 0


def main() -> int:
    try:
        return asyncio.run(async_main(parse_args()))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
