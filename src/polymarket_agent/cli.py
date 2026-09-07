from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from polymarket_agent.config import assert_no_trading_secrets, load_settings
from polymarket_agent.data.crypto_feed import CryptoPriceFeed
from polymarket_agent.data.polymarket_feed import PolymarketFeed
from polymarket_agent.data.storage import Storage
from polymarket_agent.discovery.polymarket import PolymarketDiscovery
from polymarket_agent.logging import configure_logging
from polymarket_agent.offline import OfflineDiscovery, OfflineMarketFeed, OfflinePriceFeed
from polymarket_agent.scanner.scanner import Scanner, print_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket 加密貨幣市場唯讀掃描器")
    parser.add_argument("--config", type=Path, help="settings.yaml 路徑")
    parser.add_argument("--once", action="store_true", help="只掃描一輪後結束")
    parser.add_argument("--offline", action="store_true", help="使用內建 fixtures，不呼叫網路")
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    assert_no_trading_secrets()
    settings = load_settings(args.config)
    configure_logging(settings.app.log_level, settings.app.log_path)
    storage = Storage(settings.storage.sqlite_path)
    storage.initialize()
    logging.getLogger(__name__).info(
        "Starting read-only scanner",
        extra={"read_only": settings.app.read_only, "offline": args.offline},
    )

    if args.offline:
        fixture_dir = settings.project_root / "tests/fixtures"
        discovery = OfflineDiscovery(fixture_dir)
        market_feed = OfflineMarketFeed(fixture_dir)
        price_feed = OfflinePriceFeed()
    else:
        discovery = PolymarketDiscovery(settings)
        market_feed = PolymarketFeed(settings)
        price_feed = CryptoPriceFeed(settings)

    scanner = Scanner(settings, storage, discovery, market_feed, price_feed)
    try:
        if args.once:
            print_results(await scanner.scan_once())
        else:
            await scanner.run_forever()
    finally:
        await scanner.close()
    return 0


def main() -> int:
    try:
        return asyncio.run(async_main(parse_args()))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

