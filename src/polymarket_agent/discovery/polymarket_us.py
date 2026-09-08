from __future__ import annotations

import hashlib
import logging
import math
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from polymarket_agent.config import Settings
from polymarket_agent.data.public_http import PublicAPI
from polymarket_agent.models import Market

LOGGER = logging.getLogger(__name__)
NUMBER = r"\$?([0-9][0-9,]*(?:\.\d+)?)"
DEADLINE = re.compile(r"(\d{1,2}:\d{2} [AP]M) ET on ([A-Z][a-z]+ \d{1,2}, \d{4})")


def _number(text: str) -> float:
    value = float(text.replace("$", "").replace(",", ""))
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid BTC threshold")
    return value


def _time(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError("Missing timezone")
    return value.astimezone(UTC)


def parse_rules(description: str, title: str) -> dict[str, Any]:
    """Recognize documented BRTI templates; never guess rules from year/slug."""
    issues: list[str] = []
    times = {
        datetime.strptime(f"{date} {clock}", "%B %d, %Y %I:%M %p")
        .replace(tzinfo=ZoneInfo("America/New_York"))
        .astimezone(UTC)
        for clock, date in DEADLINE.findall(description)
    }
    deadline = next(iter(times)) if len(times) == 1 else None
    if deadline is None:
        issues.append("無法唯一判定合約的觀察截止時間")
    source = (
        "CF_BENCHMARKS_BRTI"
        if (
            "CF Bitcoin Real-Time Index (BRTI)" in description
            and "trimmed mean" in description
            and "sixty second" in description
            and "20%" in description
        )
        else ""
    )
    if not source:
        issues.append("尚未支援的結算指數或計算規則")
    # Restrict interpretation to the explicit Yes condition, never the question/year.
    clause = description.split("\n", 1)[0]
    prefix = "This market will settle to Yes if the price of Bitcoin is "
    condition = clause[len(prefix) :] if clause.startswith(prefix) else ""
    kind, comparison, strike, upper = "unknown", "unknown", None, None
    touch = "at any point" in condition
    direction = re.match(r"(above|below) (.+)", condition)
    if direction:
        side, rest = direction.groups()
        match = re.match(NUMBER, rest)
        if rest.startswith("the price specified in the title"):
            match = re.fullmatch(NUMBER, title)
        if match:
            strike = _number(match.group(1))
            kind = ("touch_" if touch else "terminal_") + side
            comparison = "gt" if side == "above" else "lt"
    interval = re.match(r"between " + NUMBER + r" to " + NUMBER + r" at ", condition)
    inclusive = re.match(NUMBER + r" or (above|below) at ", condition)
    if interval and not touch:
        strike, upper = _number(interval.group(1)), _number(interval.group(2))
        if strike < upper:
            kind, comparison = "terminal_range", "closed_interval"
    elif inclusive and not touch:
        strike = _number(inclusive.group(1))
        side = inclusive.group(2)
        kind, comparison = "terminal_" + side, "ge" if side == "above" else "le"
    if kind == "unknown":
        issues.append("尚未支援的 BTC 合約條件")
    return {
        "contract_type": kind,
        "comparison": comparison,
        "strike": strike,
        "upper_strike": upper,
        "resolution_time": deadline,
        "resolution_source": source,
        "rule_issues": issues,
        "rule_hash": hashlib.sha256((title + "\n" + description).encode()).hexdigest(),
    }


def parse_us_market(payload: dict[str, Any], event: dict[str, Any] | None = None) -> Market | None:
    slug = str(payload.get("slug") or "")
    question = str(payload.get("question") or "")
    if not re.fullmatch(r"cpc-btc-[a-zA-Z0-9_-]+", slug):
        return None
    if not re.search(r"\b(bitcoin|btc)\b", question, re.IGNORECASE):
        return None
    sides = payload.get("marketSides")
    if not isinstance(sides, list) or len(sides) != 2:
        return None
    if any(not isinstance(x, dict) for x in sides):
        return None
    if {x.get("long") for x in sides} != {True, False} or any(
        x.get("marketSideType") != "MARKET_SIDE_TYPE_INSTRUMENT" or x.get("identifier") != slug
        for x in sides
    ):
        return None
    try:
        description = str(payload.get("description") or "")
        title = str(payload.get("title") or "")
        rules = parse_rules(description, title)
        settlement = _time(payload["endDate"])
        # An unreadable deadline is not replaced by the API's settlement date.
        if rules["resolution_time"] is None:
            return None
        return Market(
            market_id=f"polymarket_us:{slug}",
            venue="polymarket_us",
            instrument_id=slug,
            slug=slug,
            question=f"{question} — {title}",
            description=description,
            settlement_time=settlement,
            underlying="BTC-USD",
            category="crypto",
            active=payload.get("active") is True,
            closed=payload.get("closed") is not False or payload.get("archived") is True,
            accepting_orders=(
                payload.get("ep3Status") == "OPEN"
                and not payload.get("hidden", False)
                and all(x.get("tradable") is True for x in sides)
            ),
            volume_24h=max(float(payload.get("volume24hr") or 0), 0),
            minimum_trade_quantity=payload.get("minimumTradeQty"),
            price_tick=payload.get("orderPriceMinTickSize"),
            fee_coefficient=payload.get("feeCoefficient"),
            raw={
                "market": payload,
                "event": {
                    k: (event or {}).get(k) for k in ("slug", "title", "endDate", "description")
                },
            },
            **rules,
        )
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


class PolymarketUSDiscovery:
    def __init__(self, settings: Settings, api: PublicAPI | None = None):
        self.settings = settings
        self.api = api or PublicAPI(settings)
        self._owns_api = api is None
        self.last_stats: dict[str, Any] = {}

    async def close(self) -> None:
        if self._owns_api:
            await self.api.close()

    async def get_market(self, slug: str) -> Market:
        payload = await self.api.get(f"/v1/market/slug/{slug}")
        market = parse_us_market(payload.get("market", {}))
        if market is None:
            raise ValueError("Unsupported US BTC contract or incomplete rules")
        return market

    async def discover(self, now: datetime | None = None) -> list[Market]:
        now = now or datetime.now(UTC)
        markets: dict[str, Market] = {}
        seen_events: set[str] = set()
        rejected: Counter[str] = Counter()
        rows_seen = 0
        limit = self.settings.scanner.market_page_size
        for page in range(1, self.settings.scanner.market_max_pages + 1):
            payload = await self.api.get(
                "/v1/search", {"query": "Bitcoin", "limit": limit, "page": page}
            )
            events = payload.get("events")
            if not isinstance(events, list):
                raise TypeError("Search response lacks an events array")
            new_events = 0
            for event in events:
                if not isinstance(event, dict) or not event.get("slug"):
                    rejected["malformed_event"] += 1
                    continue
                if event["slug"] in seen_events:
                    continue
                seen_events.add(event["slug"])
                new_events += 1
                for row in event.get("markets", []):
                    rows_seen += 1
                    market = parse_us_market(row, event) if isinstance(row, dict) else None
                    if market is None:
                        rejected["unsupported_metadata"] += 1
                        continue
                    hours = (market.resolution_time - now).total_seconds() / 3600
                    if not market.active or market.closed or not market.accepting_orders:
                        rejected["not_open"] += 1
                    elif not (
                        self.settings.filters.min_hours_to_expiry
                        <= hours
                        <= self.settings.filters.max_hours_to_expiry
                    ):
                        rejected["outside_observation_horizon"] += 1
                    else:
                        markets[market.market_id] = market
            if len(events) < limit or new_events == 0:
                break
        result = sorted(markets.values(), key=lambda m: (-m.volume_24h, m.slug))
        result = result[: self.settings.scanner.max_watchlist_size]
        self.last_stats = {
            "venue": "polymarket_us",
            "events": len(seen_events),
            "rows_seen": rows_seen,
            "eligible": len(markets),
            "watchlist": len(result),
            "rejected": dict(rejected),
        }
        LOGGER.info("US market discovery completed", extra=self.last_stats)
        return result
