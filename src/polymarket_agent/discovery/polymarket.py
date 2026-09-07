from __future__ import annotations

import json
import logging
import math
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from polymarket_agent.config import Settings
from polymarket_agent.models import Market

LOGGER = logging.getLogger(__name__)

STRIKE_RE = re.compile(
    r"(?:\$|USD\s*)?([0-9][0-9,]*(?:\.\d+)?)\s*([kKmM])?",
)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _decode_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def infer_underlying(text: str) -> str | None:
    lowered = text.lower()
    if "bitcoin" in lowered or re.search(r"\bbtc\b", lowered):
        return "BTC-USD"
    if re.search(r"\b(?:ethereum|ether|eth)\b", lowered):
        return "ETH-USD"
    return None


def contains_keyword(text: str, keyword: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(keyword.lower())}(?!\w)", text.lower()) is not None


def infer_strike(text: str, underlying: str | None) -> float | None:
    if not underlying:
        return None
    candidates: list[float] = []
    for match in STRIKE_RE.finditer(text):
        number = _as_float(match.group(1).replace(",", ""), -1)
        if number < 0:
            continue
        suffix = (match.group(2) or "").lower()
        multiplier = 1_000 if suffix == "k" else 1_000_000 if suffix == "m" else 1
        value = number * multiplier
        if value >= (1_000 if underlying == "BTC-USD" else 100):
            candidates.append(value)
    return candidates[0] if candidates else None


def parse_market(payload: dict[str, Any]) -> Market | None:
    labels = [str(item).lower() for item in _decode_array(payload.get("outcomes"))]
    tokens = [str(item) for item in _decode_array(payload.get("clobTokenIds"))]
    if len(labels) < 2 or len(tokens) < 2:
        return None
    try:
        yes_index = labels.index("yes")
        no_index = labels.index("no")
    except ValueError:
        return None

    resolution_time = _parse_datetime(
        payload.get("endDate") or payload.get("endDateIso") or payload.get("end_date")
    )
    condition_id = str(payload.get("conditionId") or "")
    market_id = str(payload.get("id") or condition_id)
    question = str(payload.get("question") or payload.get("title") or "")
    if not resolution_time or not condition_id or not market_id or not question:
        return None

    combined = " ".join(
        [question, str(payload.get("description") or ""), str(payload.get("slug") or "")]
    )
    underlying = infer_underlying(combined)
    return Market(
        market_id=market_id,
        condition_id=condition_id,
        slug=str(payload.get("slug") or ""),
        question=question,
        description=str(payload.get("description") or ""),
        category=str(payload.get("category") or "crypto"),
        yes_token_id=tokens[yes_index],
        no_token_id=tokens[no_index],
        resolution_time=resolution_time,
        liquidity=_as_float(payload.get("liquidityNum", payload.get("liquidity"))),
        volume_24h=_as_float(payload.get("volume24hr", payload.get("volume24h"))),
        active=bool(payload.get("active", True)),
        closed=bool(payload.get("closed", False)),
        accepting_orders=bool(payload.get("acceptingOrders", True)),
        strike=infer_strike(question, underlying),
        underlying=underlying,
        raw=payload,
    )


class PolymarketDiscovery:
    """Discovers public markets using Gamma GET endpoints only."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=settings.api.gamma_base_url,
            timeout=settings.api.timeout_seconds,
            headers={"User-Agent": settings.api.user_agent},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def discover(self, now: datetime | None = None) -> list[Market]:
        now = now or datetime.now(UTC)
        discovered: list[Market] = []
        page_size = self.settings.scanner.market_page_size

        for page in range(self.settings.scanner.market_max_pages):
            response = await self.client.get(
                "/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "archived": "false",
                    "limit": page_size,
                    "offset": page * page_size,
                    "order": "volume24hr",
                    "ascending": "false",
                },
            )
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                raise TypeError("Gamma /markets returned a non-list response")
            for row in rows:
                if isinstance(row, dict):
                    market = parse_market(row)
                    if market and self._passes_filters(market, now):
                        discovered.append(market)
            if len(rows) < page_size:
                break

        discovered.sort(key=self.discovery_rank, reverse=True)
        watchlist = discovered[: self.settings.scanner.max_watchlist_size]
        LOGGER.info(
            "Market discovery completed",
            extra={"candidate_count": len(discovered), "watchlist_count": len(watchlist)},
        )
        return watchlist

    def _passes_filters(self, market: Market, now: datetime) -> bool:
        text = f"{market.question} {market.description} {market.slug}".lower()
        is_crypto = market.underlying is not None or any(
            contains_keyword(text, keyword) for keyword in self.settings.filters.crypto_keywords
        )
        hours = (market.resolution_time - now).total_seconds() / 3600
        return all(
            [
                is_crypto,
                market.active,
                not market.closed,
                market.accepting_orders,
                market.liquidity >= self.settings.filters.min_liquidity_usd,
                market.volume_24h >= self.settings.filters.min_volume_24h_usd,
                self.settings.filters.min_hours_to_expiry <= hours,
                hours <= self.settings.filters.max_hours_to_expiry,
            ]
        )

    @staticmethod
    def discovery_rank(market: Market) -> float:
        return math.log1p(market.liquidity) + 0.75 * math.log1p(market.volume_24h)
