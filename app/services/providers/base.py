"""Provider foundations: protocol, async HTTP client with rate limit, cache, retry/backoff."""

import asyncio
import hashlib
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ProviderUnavailable(ProviderError):
    """Provider is not configured (e.g. missing API key / user agent)."""


class BaseProvider(Protocol):
    name: str

    async def healthcheck(self) -> bool: ...

    async def aclose(self) -> None: ...


@dataclass
class RequestStats:
    http_requests: int = 0
    cache_hits: int = 0
    retries: int = 0


class RateLimiter:
    """Spaces calls at least 1/per_second apart (shared across concurrent tasks)."""

    def __init__(
        self,
        per_second: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.interval = 1.0 / per_second
        self._next = 0.0
        self._lock = asyncio.Lock()
        self._sleep, self._clock = sleep, clock

    async def wait(self) -> None:
        async with self._lock:
            now = self._clock()
            at = max(now, self._next)
            self._next = at + self.interval
            if at > now:
                await self._sleep(at - now)


class FileCache:
    """Tiny TTL cache on disk (one JSON file per key). Disabled when directory is None."""

    def __init__(self, directory: str | Path | None) -> None:
        self.dir = Path(directory) if directory else None
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path | None:
        return self.dir / (hashlib.sha256(key.encode()).hexdigest() + ".json") if self.dir else None

    def get(self, key: str) -> str | None:
        p = self._path(key)
        if not p or not p.exists():
            return None
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return obj["body"] if obj.get("exp", 0) > time.time() else None

    def set(self, key: str, body: str, ttl: float) -> None:
        p = self._path(key)
        if p and ttl > 0:
            p.write_text(json.dumps({"exp": time.time() + ttl, "body": body}), encoding="utf-8")


class HttpClient:
    """GET-only async client. Secrets in params (api_key) are never part of cache keys or errors."""

    def __init__(
        self,
        name: str,
        *,
        user_agent: str,
        per_second: float,
        timeout: float = 20.0,
        retries: int = 3,
        backoff: float = 0.8,
        cache: FileCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.name = name
        self.limiter = RateLimiter(per_second, sleep)
        self.cache = cache or FileCache(None)
        self.retries = retries
        self.backoff = backoff
        self._sleep = sleep
        self.stats = RequestStats()
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _key(self, url: str, params: dict[str, Any] | None) -> str:
        safe = {k: v for k, v in sorted((params or {}).items()) if "key" not in k.lower()}
        return f"{self.name}|{url}|{safe}"

    async def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        ttl: float = 0,
        max_bytes: int | None = None,
    ) -> str:
        key = self._key(url, params)
        cached = self.cache.get(key)
        if cached is not None:
            self.stats.cache_hits += 1
            return cached
        last: str = ""
        for attempt in range(self.retries + 1):
            await self.limiter.wait()
            self.stats.http_requests += 1
            try:
                async with self._client.stream("GET", url, params=params) as resp:
                    if resp.status_code in RETRY_STATUS and attempt < self.retries:
                        last = f"HTTP {resp.status_code}"
                        delay = self._delay(attempt, resp.headers.get("Retry-After"))
                    elif resp.status_code >= 400:
                        raise ProviderError(
                            f"{self.name}: HTTP {resp.status_code} for {url}", resp.status_code
                        )
                    else:
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in resp.aiter_bytes():
                            chunks.append(chunk)
                            size += len(chunk)
                            if max_bytes and size >= max_bytes:
                                break
                        raw = b"".join(chunks)[: max_bytes or None]
                        body = raw.decode(resp.encoding or "utf-8", errors="replace")
                        self.cache.set(key, body, ttl)
                        return body
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last = type(exc).__name__
                if attempt >= self.retries:
                    break
                delay = self._delay(attempt, None)
            self.stats.retries += 1
            await self._sleep(delay)
        raise ProviderError(f"{self.name}: giving up on {url} ({last})")

    async def get_json(self, url: str, **kw: Any) -> Any:
        text = await self.get_text(url, **kw)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ProviderError(f"{self.name}: invalid JSON from {url}") from exc

    def _delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 60.0)
        return self.backoff * (2**attempt) + random.uniform(0, self.backoff / 4)
