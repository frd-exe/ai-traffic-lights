"""GET /api/geocode: Nominatim proxy. Usage policy: identifying User-Agent, <= 1 request/s, cache.

The HTTP client is injectable (tests use httpx.MockTransport; they never touch the network).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from backend.api_common import ApiError
from backend.contract.constants import NOMINATIM_MIN_INTERVAL_S
from backend.contract.models import BBox, GeocodeResponse, GeocodeResult

log = logging.getLogger(__name__)
DEFAULT_URL = "https://nominatim.openstreetmap.org/search"
MAX_RESULTS = 5
MAX_QUERY_LEN = 200


def user_agent() -> str:
    contact = os.environ.get("NOMINATIM_CONTACT") or os.environ.get("OVERPASS_CONTACT") or "hackathon prototype"
    return f"ai-traffic-lights-hackathon/0.2 ({contact})"


def _parse(items: object) -> list[GeocodeResult]:
    if not isinstance(items, list):
        raise ValueError("expected a JSON list")
    out = []
    for it in items[:MAX_RESULTS]:
        try:
            bb = None
            if isinstance(it.get("boundingbox"), list) and len(it["boundingbox"]) == 4:
                s, n, w, e = (float(x) for x in it["boundingbox"])
                if w < e and s < n:
                    bb = BBox(west=w, south=s, east=e, north=n)
            out.append(GeocodeResult(display_name=str(it.get("display_name", "")),
                                     lat=float(it["lat"]), lon=float(it["lon"]), bbox=bb))
        except (KeyError, TypeError, ValueError):
            continue
    return out


class Geocoder:
    def __init__(
        self,
        cache_path: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        url: str = DEFAULT_URL,
        min_interval_s: float = NOMINATIM_MIN_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.url, self.min_interval_s, self.clock, self.sleep = url, min_interval_s, clock, sleep
        self.transport = transport
        self.cache_path = cache_path
        self._cache: dict[str, list[dict]] = {}
        if cache_path and cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text("utf-8"))
            except ValueError:
                log.warning("ignoring corrupt geocode cache %s", cache_path)
        self._lock = asyncio.Lock()
        self._last_call: float | None = None
        self.upstream_calls = 0

    def _save(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_bytes(json.dumps(self._cache, sort_keys=True).encode("utf-8"))

    async def search(self, q: str) -> GeocodeResponse:
        key = " ".join(q.strip().lower().split())
        if not key:
            raise ApiError(400, "empty_query", "q must not be empty")
        if len(key) > MAX_QUERY_LEN:
            raise ApiError(400, "query_too_long", f"q is limited to {MAX_QUERY_LEN} characters")
        if key in self._cache:
            return GeocodeResponse(results=[GeocodeResult.model_validate(r) for r in self._cache[key]])

        async with self._lock:  # serialises upstream calls => global 1 req/s
            if key in self._cache:
                return GeocodeResponse(results=[GeocodeResult.model_validate(r) for r in self._cache[key]])
            if self._last_call is not None:
                wait = self.min_interval_s - (self.clock() - self._last_call)
                if wait > 0:
                    await self.sleep(wait)
            self._last_call = self.clock()
            self.upstream_calls += 1
            try:
                async with httpx.AsyncClient(transport=self.transport, timeout=10.0,
                                             headers={"User-Agent": user_agent()}) as client:
                    r = await client.get(self.url, params={"q": q.strip(), "format": "jsonv2", "limit": MAX_RESULTS})
            except httpx.HTTPError as e:
                raise ApiError(502, "geocode_unavailable", f"geocoding service unreachable: {type(e).__name__}")
            if r.status_code != 200:
                raise ApiError(502, "geocode_unavailable", f"geocoding service returned HTTP {r.status_code}")
            try:
                results = _parse(r.json())
            except ValueError:
                raise ApiError(502, "geocode_unavailable", "geocoding service returned invalid JSON")
            self._cache[key] = [x.model_dump(mode="json") for x in results]
            self._save()
            return GeocodeResponse(results=results)
