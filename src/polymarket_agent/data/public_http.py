"""Bounded, rate-limited GET access to the unauthenticated US gateway."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from polymarket_agent.config import Settings


class PublicAPI:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=settings.api.us_base_url,
            timeout=settings.api.timeout_seconds,
            headers={"User-Agent": settings.api.user_agent},
            follow_redirects=False,
        )
        self._lock = asyncio.Lock()
        self._next_request = 0.0

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _throttle(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(self._next_request - loop.time(), 0))
            self._next_request = loop.time() + 1 / self.settings.api.public_requests_per_second

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if path not in {"/v1/search", "/v1/price-history"} and not re.fullmatch(
            r"/v1/(?:markets/[a-zA-Z0-9_-]+/book|markets/cpc-btc-[a-zA-Z0-9_-]+/settlement|market/slug/cpc-btc-[a-zA-Z0-9_-]+)", path
        ):
            raise ValueError("Only public market GET routes are allowed")
        attempts = self.settings.api.retry_attempts
        for attempt in range(attempts):
            await self._throttle()
            try:
                response = await self.client.get(path, params=params)
            except httpx.TransportError:
                if attempt + 1 == attempts:
                    raise
            else:
                if response.status_code == 429:
                    raw = response.headers.get("Retry-After", "1")
                    try:
                        delay = float(raw)
                    except ValueError:
                        try:
                            delay = (parsedate_to_datetime(raw) - datetime.now(UTC)).total_seconds()
                        except (ValueError, TypeError):
                            delay = 1.0
                    self._next_request = max(
                        self._next_request, asyncio.get_running_loop().time() + max(delay, 1)
                    )
                if response.status_code not in {429, 500, 502, 503, 504} or attempt + 1 == attempts:
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError("US gateway response must be an object")
                    return payload
            await asyncio.sleep(self.settings.api.retry_base_seconds * 2**attempt)
        raise RuntimeError("Unreachable retry state")
