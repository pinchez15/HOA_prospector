"""Shared HTTP client with retry logic and rate limiting."""

from __future__ import annotations

import asyncio
import logging

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import MAX_RETRIES, REQUEST_DELAY, REQUEST_TIMEOUT, USER_AGENT

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class RateLimitedClient:
    """Async HTTP client with per-domain rate limiting and retries."""

    def __init__(self, delay: float = REQUEST_DELAY):
        self.delay = delay
        self._last_request: float = 0
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=_DEFAULT_HEADERS,
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        return self._client

    async def _wait_for_rate_limit(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_request
            if elapsed < self.delay:
                await asyncio.sleep(self.delay - elapsed)
            self._last_request = asyncio.get_event_loop().time()

    @retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(min=1, max=10))
    async def get(self, url: str, params: dict | None = None, **kwargs) -> httpx.Response:
        await self._wait_for_rate_limit()
        client = await self._get_client()
        logger.debug(f"GET {url} params={params}")
        resp = await client.get(url, params=params, **kwargs)
        resp.raise_for_status()
        return resp

    @retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(min=1, max=10))
    async def post(self, url: str, data: dict | None = None, **kwargs) -> httpx.Response:
        await self._wait_for_rate_limit()
        client = await self._get_client()
        logger.debug(f"POST {url}")
        resp = await client.post(url, data=data, **kwargs)
        resp.raise_for_status()
        return resp

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
