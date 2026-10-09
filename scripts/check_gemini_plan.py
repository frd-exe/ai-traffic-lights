"""ONE live Gemini supervisor call with a real observation from the demo city (run locally).

    python scripts/check_gemini_plan.py

Reads GEMINI_API_KEY / GEMINI_MODEL from .env, runs the demo city for 60 sim-s under rush demand
(max-pressure), sends the compact observation once, and prints the validated plans. The key is
never printed. Uses 1-2 calls of your GEMINI_DAILY_CAP budget.

Expected output: "OK: N plan(s)" followed by a JSON array of
{"intersection_id": "i_...", "phase": "i_...:p0|p1", "hold_s": 5-40, "reason": "..."}.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from backend.ai.gemini_client import GeminiClient, GeminiError
    from backend.app import Settings
    from backend.area.service import AreaService
    from backend.control.ai_gemini import compact_observation
    from backend.control.runner import run_headless
    from backend.sim import SimEngine
    from backend.traffic.demand import build_profile

    s = Settings()
    if not s.env.get("GEMINI_API_KEY"):
        print("FAIL: GEMINI_API_KEY is empty in .env")
        return 1
    area = AreaService(s.grid_path, ROOT / "no_sample.json", s.state_dir / "areas").demo_area()[0]
    profile = build_profile(area_id=area.area_id, entry_nodes=list(area.network.entry_nodes), level="rush")
    engine = SimEngine(area.network, area.intersections, 1, profile, [i.id for i in area.intersections])
    run_headless(engine, "max_pressure", 60)
    obs = engine.observe()
    env = {**s.env, "GEMINI_FAKE_FAIL": "0"}
    client = GeminiClient.from_env(s.state_dir, env)
    allowed = {iid: [p.id for p in st.phases] for iid, st in obs.intersections.items()}
    t0 = time.perf_counter()
    try:
        plans = asyncio.run(client.plan(compact_observation(obs), allowed))
    except GeminiError as e:
        print(f"FAIL ({e.kind}): {e.message}")
        print({"limit": "Quota/rate limit: wait, or check your plan in Google AI Studio.",
               "invalid_key": "Check GEMINI_API_KEY in .env (python scripts/check_keys.py).",
               "daily_cap": "Our GEMINI_DAILY_CAP is used up: POST /api/ai/reset or raise the cap.",
               }.get(e.kind, "Network/model problem: retry; try GEMINI_MODEL=gemini-3.5-flash-lite."))
        return 1
    print(f"OK: {len(plans)} plan(s) from {client.model} in {time.perf_counter() - t0:.1f} s "
          f"(calls today: {client.usage.calls_today()}/{client.daily_cap})")
    print(json.dumps([p.model_dump() for p in plans], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
