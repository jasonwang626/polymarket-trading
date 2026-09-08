"""Collect book-derived display prices; this does not simulate fills or P&L."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_agent.config import assert_no_trading_secrets, load_settings
from polymarket_agent.data.polymarket_us import PolymarketUSFeed
from polymarket_agent.data.public_http import PublicAPI
from polymarket_agent.data.storage import Storage
from polymarket_agent.discovery.polymarket_us import PolymarketUSDiscovery


async def collect(args: argparse.Namespace) -> dict:
    assert_no_trading_secrets()
    settings = load_settings(args.config)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    if not 0 < (end - start).days <= 366 or end > datetime.now(UTC):
        raise ValueError("Use a past UTC date range of 1..366 days; end is exclusive")
    db = Storage(args.database or settings.storage.sqlite_path)
    db.initialize()
    api = PublicAPI(settings)
    feed = PolymarketUSFeed(settings, api)
    discovery = PolymarketUSDiscovery(settings, api)
    try:
        market = await discovery.get_market(args.slug)
        db.upsert_market(market, datetime.now(UTC))
        days = []
        current = start
        while current < end:
            following = current + timedelta(days=1)
            points = await feed.get_price_history(market, start=current, end=following)
            inserted = db.insert_quote_history(points, datetime.now(UTC))
            days.append(
                {
                    "start": current.isoformat(),
                    "points": len(points),
                    "inserted": inserted,
                    "crossed_display_points": sum(p.is_crossed for p in points),
                    "first": points[0].timestamp.isoformat() if points else None,
                    "last": points[-1].timestamp.isoformat() if points else None,
                }
            )
            current = following
        return {
            "venue": market.venue,
            "market_id": market.market_id,
            "data_kind": "book_derived_display_asks_not_trades",
            "start_inclusive": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "points_received": sum(d["points"] for d in days),
            "points_inserted": sum(d["inserted"] for d in days),
            "days": days,
        }
    finally:
        await api.close()


if __name__ == "__main__":
    today = datetime.now(UTC).date()
    parser = argparse.ArgumentParser(description="收集美國版 BTC 歷史顯示報價；不是績效回測")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--start", default=str(today - timedelta(days=30)), help="UTC 日期，含當天")
    parser.add_argument("--end", default=str(today), help="UTC 日期，不含當天")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--config", type=Path)
    print(json.dumps(asyncio.run(collect(parser.parse_args())), ensure_ascii=False, indent=2))
