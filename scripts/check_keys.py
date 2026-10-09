"""Check API keys in .env with one tiny call each. Never prints the keys.

    python scripts/check_keys.py

Gemini: GET models list (pageSize=1). Google Maps: one Routes API computeRoutes call
(TRAFFIC_AWARE, field mask duration,staticDuration) - this counts against your Routes quota.
Stdlib only; run locally (needs internet).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_S = 15


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def request(url: str, headers: dict[str, str], body: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, f"network error: {getattr(e, 'reason', e)}"


def reason(status: int, body: str) -> str:
    try:
        err = json.loads(body).get("error", {})
        return f"HTTP {status} {err.get('status', '')} {err.get('message', '')[:160]}".strip()
    except (ValueError, AttributeError):
        return f"HTTP {status} {body[:160]}" if status else body[:160]


def check_gemini(key: str) -> tuple[bool, str]:
    status, body = request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
                           {"x-goog-api-key": key})
    return status == 200, reason(status, body) if status != 200 else "models list OK"


def check_routes(key: str) -> tuple[bool, str]:
    body = {
        "origin": {"location": {"latLng": {"latitude": 41.3900, "longitude": 2.1650}}},
        "destination": {"location": {"latLng": {"latitude": 41.3930, "longitude": 2.1700}}},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
    }
    status, text = request("https://routes.googleapis.com/directions/v2:computeRoutes", {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "routes.duration,routes.staticDuration",
    }, body)
    if status != 200:
        return False, reason(status, text)
    try:
        r = json.loads(text)["routes"][0]
        return True, f"duration={r.get('duration')} staticDuration={r.get('staticDuration')}"
    except (KeyError, IndexError, ValueError):
        return False, "HTTP 200 but no route in response"


def main() -> int:
    env = load_env(ROOT / ".env")
    if not env:
        print("No .env found. Copy .env.example to .env and add your keys.")
        return 1
    secrets = [env.get("GEMINI_API_KEY", ""), env.get("GOOGLE_MAPS_API_KEY", "")]
    ok_all = True
    for name, var, fn, required in (("Gemini", "GEMINI_API_KEY", check_gemini, True),
                                    ("Google Routes", "GOOGLE_MAPS_API_KEY", check_routes, False)):
        key = env.get(var, "")
        if not key and not required:
            print(f"{name:14} SKIP  {var} is empty (optional: not used since contract 0.2.0)")
            continue
        if not key:
            print(f"{name:14} FAIL  {var} is empty")
            ok_all = False
            continue
        ok, info = fn(key)
        ok_all &= ok
        print(f"{name:14} {'OK  ' if ok else 'FAIL'}  {scrub(info, secrets)}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
