"""Gemini client for the AI supervisor. Never logs or returns the API key.

- DAILY budget guard: GEMINI_DAILY_CAP calls per UTC day, counter persisted to
  <STATE_DIR>/usage.json (survives restarts). Checked BEFORE every call; every attempt that is
  sent counts (including retries and probes). POST /api/ai/reset zeroes today's counter.
- Rate limiter: >= GEMINI_MIN_INTERVAL_S between calls and <= GEMINI_RPM calls per rolling
  minute (both wall-clock; callers await, they are never rejected).
- Error classification (GeminiError.kind):
    daily_cap      our own cap reached (no network call made)
    limit          HTTP 429 / RESOURCE_EXHAUSTED
    invalid_key    missing key, HTTP 401/403, or 400 with API_KEY_INVALID / "API key not valid"
    transient      timeout, network error, HTTP 5xx (retried once inside plan())
    invalid_output response not parseable into a JSON array of plans (retried once)
- GEMINI_FAKE_FAIL=limit|timeout|invalid|badjson|5xx (aliases: 429 -> limit, key -> invalid)
  injects failures without any network access. "0" / empty = off.
- Low latency: flash-class model from GEMINI_MODEL, JSON-only output enforced with a response
  schema (ARRAY of {intersection_id, phase, hold_s, reason}), thinking budget 0, temperature 0.2.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import httpx
from pydantic import ValidationError

from backend.contract.constants import GEMINI_MIN_INTERVAL_S
from backend.contract.models import Plan

log = logging.getLogger("backend.ai")
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# gemini-2.5-flash is no longer offered to new users (404, Oct 2026). Measured on the demo city:
# gemini-3.5-flash-lite 2.5 s per call; gemini-3.8-flash ~14 s (over the 8 s timeout); 3.5-flash 503 (overloaded).
DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_THINKING_LEVEL = "low"  # Gemini 3 models ("minimal" is rejected by 3.x flash)
DEFAULT_DAILY_CAP = 500
DEFAULT_RPM = 10
TIMEOUT_S = 8.0
HOLD_MIN_S, HOLD_MAX_S = 5.0, 40.0
ErrorKind = Literal["daily_cap", "limit", "invalid_key", "transient", "invalid_output"]
FAKE_ALIASES = {"429": "limit", "limit": "limit", "key": "invalid", "invalid": "invalid", "timeout": "timeout",
                "5xx": "5xx", "badjson": "badjson"}

SYSTEM_PROMPT = """You supervise adaptive traffic signals in a simulation.
Input: JSON with sim time t and, per signalised intersection, its phases (phase id -> approach ids
that are green together), the current phase, seconds in phase, and per approach:
q = stopped vehicles near the stop line, n = vehicles on the approach, w = longest current wait (s),
down = vehicles already on the approach's exits (spillback risk).
Goal: minimise total waiting time. Give green to busy approaches, keep the others red. If an
intersection has no traffic, keep its current phase (do not flap). Every phase change costs 5 s
of yellow + all-red, so do not switch for small differences. Never let an approach with waiting
vehicles stay red near 60 s. The engine enforces all safety rules; you only choose phases.
Return ONLY a JSON array with one object per intersection you want to control:
{"intersection_id": str, "phase": one of that intersection's phase ids, "hold_s": number 5-40 (the
MAXIMUM time to keep that phase), "reason": short plain-English explanation (<= 15 words)}."""

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "intersection_id": {"type": "STRING"},
            "phase": {"type": "STRING"},
            "hold_s": {"type": "NUMBER"},
            "reason": {"type": "STRING"},
        },
        "required": ["intersection_id", "phase", "hold_s", "reason"],
    },
}


class GeminiError(Exception):
    def __init__(self, kind: ErrorKind, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class UsageCounter:
    """Calls per UTC day, persisted as JSON. Thread-safe."""

    def __init__(self, path: Path | None, daily_cap: int, today: Callable[[], str] = _today):
        self.path, self.daily_cap, self.today = path, daily_cap, today
        self._lock = threading.Lock()
        self._data = {"date": today(), "calls": 0}
        if path and path.exists():
            try:
                self._data = {**self._data, **json.loads(path.read_text("utf-8"))}
            except ValueError:
                log.warning("ignoring corrupt usage file %s", path)

    def _roll(self) -> None:
        if self._data.get("date") != self.today():
            self._data = {"date": self.today(), "calls": 0}

    def _save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data), "utf-8")
            os.replace(tmp, self.path)

    def calls_today(self) -> int:
        with self._lock:
            self._roll()
            return int(self._data["calls"])

    def allowed(self) -> bool:
        return self.calls_today() < self.daily_cap

    def increment(self) -> int:
        with self._lock:
            self._roll()
            self._data["calls"] = int(self._data["calls"]) + 1
            self._save()
            return self._data["calls"]

    def reset(self) -> None:
        with self._lock:
            self._data = {"date": self.today(), "calls": 0, "reset_at": datetime.now(timezone.utc).isoformat()}
            self._save()


def parse_plans(items: Any, allowed: dict[str, list[str]]) -> list[Plan]:
    """Validate model output. Unknown intersections/phases are dropped; hold_s is clamped to [5, 40].
    Raises GeminiError(invalid_output) if the output is not a list or nothing usable remains."""
    if not isinstance(items, list):
        raise GeminiError("invalid_output", "model output is not a JSON array")
    plans: list[Plan] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        iid, phase = it.get("intersection_id"), it.get("phase")
        if iid not in allowed or phase not in allowed[iid]:
            continue
        try:
            hold = min(HOLD_MAX_S, max(HOLD_MIN_S, float(it.get("hold_s", HOLD_MIN_S))))
            plans.append(Plan(intersection_id=iid, phase=phase, hold_s=hold,
                              reason=str(it.get("reason", ""))[:200] or "no reason given"))
        except (TypeError, ValueError, ValidationError):
            continue
    if items and not plans:
        raise GeminiError("invalid_output", "model output contained no valid plan")
    return plans


class GeminiClient:
    def __init__(
        self,
        api_key: str | None,
        usage: UsageCounter,
        model: str = DEFAULT_MODEL,
        fake_fail: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = TIMEOUT_S,
        min_interval_s: float = GEMINI_MIN_INTERVAL_S,
        rpm: int = DEFAULT_RPM,
        thinking_budget: int | None = 0,
        thinking_level: str | None = DEFAULT_THINKING_LEVEL,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ):
        self._key = api_key or ""
        self.usage, self.model, self.transport, self.timeout_s = usage, model, transport, timeout_s
        ff = (fake_fail or "").strip().lower()
        self.fake_fail = None if ff in ("", "0", "off", "none") else FAKE_ALIASES.get(ff, ff)
        self.min_interval_s, self.rpm, self.thinking_budget = min_interval_s, rpm, thinking_budget
        self.thinking_level = thinking_level
        self.clock, self.sleep = clock, sleep
        self._calls: deque[float] = deque()
        self._last_call: float | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def from_env(cls, state_dir: Path, env: dict[str, str] | None = None, **kw: Any) -> "GeminiClient":
        env = dict(os.environ if env is None else env)
        cap = int(env.get("GEMINI_DAILY_CAP") or DEFAULT_DAILY_CAP)
        budget = env.get("GEMINI_THINKING_BUDGET", "0")
        level = env.get("GEMINI_THINKING_LEVEL", DEFAULT_THINKING_LEVEL)
        return cls(
            api_key=env.get("GEMINI_API_KEY"),
            usage=UsageCounter(state_dir / "usage.json", cap),
            model=env.get("GEMINI_MODEL") or DEFAULT_MODEL,
            fake_fail=env.get("GEMINI_FAKE_FAIL"),
            min_interval_s=float(env.get("GEMINI_MIN_INTERVAL_S") or GEMINI_MIN_INTERVAL_S),
            rpm=int(env.get("GEMINI_RPM") or DEFAULT_RPM),
            thinking_budget=None if budget.lower() in ("", "none") else int(budget),
            thinking_level=None if level.lower() in ("", "none") else level,
            **kw,
        )

    # ---------------------------------------------------------------- bookkeeping
    @property
    def daily_cap(self) -> int:
        return self.usage.daily_cap

    def calls_last_min(self) -> int:
        now = self.clock()
        while self._calls and now - self._calls[0] >= 60:
            self._calls.popleft()
        return len(self._calls)

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    async def _pace(self) -> None:
        while True:
            now = self.clock()
            waits = []
            if self._last_call is not None:
                waits.append(self.min_interval_s - (now - self._last_call))
            if self.rpm and self.calls_last_min() >= self.rpm:
                waits.append(60 - (now - self._calls[0]))
            wait = max(waits, default=0.0)
            if wait <= 0:
                return
            await self.sleep(wait)

    # ---------------------------------------------------------------- calls
    async def plan(self, payload: dict, allowed: dict[str, list[str]]) -> list[Plan]:
        """One supervisor call (with one retry on transient/invalid output)."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:  # one lock per event loop
            self._lock, self._lock_loop = asyncio.Lock(), loop
        async with self._lock:
            last: GeminiError | None = None
            for attempt in range(2):
                try:
                    return await self._attempt(payload, allowed)
                except GeminiError as e:
                    if e.kind not in ("transient", "invalid_output"):
                        raise
                    last = e
                    log.info("gemini attempt %d failed (%s): %s", attempt + 1, e.kind, e.message)
            assert last is not None
            raise last

    async def _attempt(self, payload: dict, allowed: dict[str, list[str]]) -> list[Plan]:
        if not self.usage.allowed():
            raise GeminiError("daily_cap", f"daily cap of {self.usage.daily_cap} Gemini calls reached")
        if not self._key and self.fake_fail is None:
            raise GeminiError("invalid_key", "GEMINI_API_KEY is not set")
        await self._pace()
        self._last_call = self.clock()
        self._calls.append(self._last_call)
        self.usage.increment()
        if self.fake_fail:
            return await self._fake(allowed)
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(payload, separators=(",", ":"))}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA,
                "temperature": 0.2,
                "maxOutputTokens": 2048,
                **self._thinking_config(),
            },
        }
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout_s) as client:
                r = await client.post(API_URL.format(model=self.model), json=body,
                                      headers={"x-goog-api-key": self._key})
        except httpx.TimeoutException:
            raise GeminiError("transient", f"timeout after {self.timeout_s:.0f} s")
        except httpx.HTTPError as e:
            raise GeminiError("transient", f"network error: {type(e).__name__}")
        return self._handle(r.status_code, r.text, allowed)

    def _thinking_config(self) -> dict:
        """Gemini 2.x: thinkingBudget (0 = off). Gemini 3+: thinkingLevel (budget is not supported)."""
        if self.model.startswith("gemini-2"):
            return {"thinkingConfig": {"thinkingBudget": self.thinking_budget}} if self.thinking_budget is not None else {}
        return {"thinkingConfig": {"thinkingLevel": self.thinking_level}} if self.thinking_level else {}

    def _handle(self, status: int, text: str, allowed: dict[str, list[str]]) -> list[Plan]:
        text = self._scrub(text)
        if status == 200:
            try:
                data = json.loads(text)
                out = data["candidates"][0]["content"]["parts"][0]["text"]
                return parse_plans(json.loads(out), allowed)
            except (ValueError, KeyError, IndexError, TypeError) as e:
                raise GeminiError("invalid_output", f"unparseable response ({type(e).__name__})")
        detail = ""
        try:
            err = json.loads(text).get("error", {})
            detail = f"{err.get('status', '')} {str(err.get('message', ''))[:160]}".strip()
        except (ValueError, AttributeError):
            detail = text[:160]
        if status == 429 or "RESOURCE_EXHAUSTED" in text:
            raise GeminiError("limit", f"HTTP {status} {detail}")
        if status in (401, 403) or "API_KEY_INVALID" in text or "API key not valid" in text:
            raise GeminiError("invalid_key", f"HTTP {status} {detail}")
        if status >= 500:
            raise GeminiError("transient", f"HTTP {status} {detail}")
        raise GeminiError("invalid_output", f"HTTP {status} {detail}")

    async def _fake(self, allowed: dict[str, list[str]]) -> list[Plan]:
        await asyncio.sleep(0)
        kind = self.fake_fail
        if kind == "limit":
            raise GeminiError("limit", "HTTP 429 RESOURCE_EXHAUSTED (GEMINI_FAKE_FAIL)")
        if kind == "invalid":
            raise GeminiError("invalid_key", "HTTP 400 API_KEY_INVALID (GEMINI_FAKE_FAIL)")
        if kind == "timeout":
            raise GeminiError("transient", f"timeout after {self.timeout_s:.0f} s (GEMINI_FAKE_FAIL)")
        if kind == "5xx":
            raise GeminiError("transient", "HTTP 503 UNAVAILABLE (GEMINI_FAKE_FAIL)")
        if kind == "badjson":
            raise GeminiError("invalid_output", "model output is not a JSON array (GEMINI_FAKE_FAIL)")
        raise GeminiError("transient", f"unknown GEMINI_FAKE_FAIL value {kind!r}")
