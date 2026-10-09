"""Controller comparison on the demo city -> docs/results.md (+ docs/results.json).

    python -m backend.experiments.compare                            # everything, incl. live Gemini rows
    python -m backend.experiments.compare --skip-ai                  # fixed / webster / max_pressure only
    python -m backend.experiments.compare --only-ai --ai-pace 10     # (re)run only the Gemini rows
    python -m backend.experiments.compare --render-only              # rebuild results.md from results.json

Setup: the demo city with the SAME 3 signalised junctions in every mode: the simulation-based siting
top 3 (`siting_top`, also what the UI preselects). Every other junction is unsignalised.
Scenarios: low, medium, high, rush, plus surge (rush, then from t=SURGE_AT_S a 2x override on
SURGE_ENTRY via a demand segment). 10 simulated minutes per run, seeds 1-3.

Metrics per run: avg_wait_s and trip_delay_s (contract metrics at the end of the run), throughput =
genuine trip completions per minute over the whole run, max queue = most stopped vehicles on any
single approach (sampled every 10 sim-s), plus external queue and deadlocks.

Gemini runs: supervisor scheduled on SIM time (every 20 sim-s), live API, paced to --ai-pace calls/min.
If a run hits the AI limit / fallback, limit_reached_at_t is recorded and the row is INVALID (never
averaged). The LLM is non-deterministic: n and sd are reported.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"
SCENARIOS = ["low", "medium", "high", "rush", "surge"]
MODES = ["fixed", "webster", "max_pressure", "ai"]
LABEL = {"fixed": "fixed (30 s)", "webster": "webster", "max_pressure": "max_pressure", "ai": "gemini"}
SURGE_AT_S = 300.0
SURGE_ENTRY = "n20"  # west end of the primary street
SURGE_FACTOR = 2.0
DURATION_S = 600.0
SEEDS = [1, 2, 3]
AI_DEMANDS = ["low", "medium", "high", "rush"]
AI_SEEDS = [1, 2, 3]
SITING_SEED, SITING_LEVEL = 42, "rush"


def _area():
    """Demo city with the committed simulation-based siting applied (recommended_ids = top 3)."""
    from backend.contract.models import AreaResponse, RoadNetwork
    from backend.roadnet.intersections import build_intersections
    from backend.roadnet.sim_siting import SitingCache, apply_to_area
    from backend.siting.prefilter import rank

    net = RoadNetwork.model_validate_json((ROOT / "backend" / "data" / "grid_network.json").read_text("utf-8"))
    ranked, rec = rank(net, build_intersections(net))
    area = AreaResponse(area_id="area_grid_mock", source="synthetic_grid", network=net, intersections=ranked,
                        recommended_ids=rec)
    res = SitingCache(ROOT / "backend" / "data" / "state" / "siting").get(area.area_id, SITING_SEED, SITING_LEVEL)
    if res is None:
        raise SystemExit("no siting result for the demo city; run: python scripts/precompute_siting.py")
    return apply_to_area(area, res)


def run_one(job: dict) -> dict:
    """One (scenario, mode, seed) run. Top-level so it can run in a worker process."""
    from backend.control.runner import run_headless
    from backend.sim import SimEngine
    from backend.traffic.demand import build_profile

    area = _area()
    net = area.network
    scenario, mode, seed = job["scenario"], job["mode"], job["seed"]
    duration = job.get("duration", DURATION_S)
    level = "rush" if scenario == "surge" else scenario
    profile = build_profile(area_id=area.area_id, entry_nodes=list(net.entry_nodes), level=level,
                            profile_id=f"dp_exp_{level}")
    selected = list(area.recommended_ids)
    engine = SimEngine(net, area.intersections, seed, profile, selected)
    if scenario == "surge":
        engine.set_demand(entry_overrides={SURGE_ENTRY: SURGE_FACTOR}, at_t=SURGE_AT_S)
    controller, client = None, None
    if mode == "ai":
        from backend.ai.gemini_client import GeminiClient
        from backend.app import Settings
        from backend.control.ai_gemini import GeminiSupervisorController

        settings = Settings()
        client = GeminiClient.from_env(settings.state_dir, settings.env)
        if job.get("ai_pace"):
            client.rpm = int(job["ai_pace"])
        controller = GeminiSupervisorController(client, seed=seed, session_label=f"exp-{scenario}-{seed}")
    t0 = time.perf_counter()
    res = run_headless(engine, mode, duration, controller=controller, ai_interval_s=job.get("ai_interval", 20.0))
    m = res.metrics
    row = {
        "scenario": scenario, "mode": mode, "seed": seed, "duration_s": duration, "signalised": selected,
        "avg_wait_s": round(m.avg_wait_s, 2), "trip_delay_s": round(m.trip_delay_s, 2),
        "exits_per_min": round(res.exits / (duration / 60), 2), "throughput_last_min": m.throughput_per_min,
        "max_queue": res.max_queue, "avg_queue_end": round(m.avg_queue, 2), "max_avg_queue": round(res.max_avg_queue, 2),
        "external_queue_end": sum(map(len, engine.external.values())), "max_external_queue": res.max_external,
        "deadlocks": m.deadlocks, "blocked_spawns": m.blocked_spawns, "generated": engine.generated,
        "safety_overrides": res.overrides, "wall_s": round(time.perf_counter() - t0, 1), "status": "filled",
    }
    if mode == "ai":
        row.update(model=client.model, ai_calls_ok=res.ai_calls_ok, ai_calls_failed=res.ai_calls_failed,
                   limit_reached_at_t=res.limit_reached_at_t,
                   effective_controller_end=controller.effective_controller)
        if res.limit_reached_at_t is not None:
            row["status"] = "INVALID"
    return row


def _ms(values: list[float], digits: int = 1) -> str:
    if not values:
        return "-"
    if len(values) == 1:
        return f"{values[0]:.{digits}f}"
    return f"{statistics.mean(values):.{digits}f} ± {statistics.stdev(values):.{digits}f}"


def _cell(rows, scen, mode):
    rs = [r for r in rows if r["scenario"] == scen and r["mode"] == mode]
    return rs, [r for r in rs if r["status"] == "filled"], [r for r in rs if r["status"] == "INVALID"]


def _status(mode, scen, rs, valid, invalid) -> str:
    if not rs:
        return "USER MUST RUN" if mode == "ai" else "NOT RUN"
    s = "filled" if valid else "INVALID"
    if invalid:
        s += f" ({len(invalid)} INVALID: limit at t=" + ", ".join(f"{r['limit_reached_at_t']:.0f}" for r in invalid) + " s)"
    return s


def summary(rows: list[dict]) -> list[str]:
    lines = []
    for scen in SCENARIOS:
        means = {}
        for mode in MODES:
            _, valid, _ = _cell(rows, scen, mode)
            if valid:
                means[mode] = statistics.mean(r["avg_wait_s"] for r in valid)
        if "fixed" not in means:
            continue
        parts = [f"{LABEL[m]} {v:.1f} s ({(v - means['fixed']) / means['fixed'] * 100:+.0f}%)"
                 for m, v in means.items() if m != "fixed"]
        lines.append(f"- **{scen}**: fixed {means['fixed']:.1f} s; " + "; ".join(parts))
    return lines


def render(rows: list[dict], meta: dict) -> str:
    sig = next((r["signalised"] for r in rows if r.get("signalised")), [])
    ai_rows = [r for r in rows if r["mode"] == "ai"]
    models = sorted({r.get("model", "?") for r in ai_rows})
    out = [
        "# Results: controller comparison (demo city)",
        "",
        f"_Generated {meta['generated_at']} by `python -m backend.experiments.compare`. Raw rows: `docs/results.json`._",
        "",
        "**Setup.** Synthetic 3×3 demo grid (`area_grid_mock`). The **same 3 junctions are signalised in every mode**: "
        f"the simulation-based siting top 3 ({', '.join(f'`{s}`' for s in sig) or '-'}), which the UI also preselects; "
        "the other 6 junctions are unsignalised (priority + gap acceptance). "
        f"{int(DURATION_S / 60)} simulated minutes per run, seeds 1–3, same seed ⇒ identical demand schedule across "
        "controllers. Simulated demand per entry: low 120 / medium 200 / high 280 / rush 380 veh/h; **surge** = rush, "
        f"then from t={SURGE_AT_S:.0f} s a {SURGE_FACTOR:g}× override on entry `{SURGE_ENTRY}`.",
        "",
        f"**Gemini** = `gemini+max_pressure`: live Gemini API ({', '.join(f'`{m}`' for m in models) or 'not run'}), "
        "one supervisor call every 20 **sim**-seconds (≈30 calls per run), plans applied on top of max-pressure. "
        "The LLM is non-deterministic, so the same seed does not give the same result twice.",
        "",
        "**Columns.** avg wait = contract `avg_wait_s` at the end of the run (mean ± sd over seeds) · throughput = trips "
        "completed per minute over the whole run · max queue = most stopped vehicles on any single approach, sampled "
        "every 10 s (max over seeds) · status: **filled** = measured · **INVALID** = Gemini hit a limit/fallback "
        "(excluded from means) · **USER MUST RUN**.",
        "",
        "## Main table",
        "",
        "| demand | controller | n | avg wait (s) | throughput (veh/min) | max queue (veh) | status |",
        "|---|---|---|---|---|---|---|",
    ]
    for scen in ["low", "medium", "high", "rush"]:
        for mode in MODES:
            rs, valid, invalid = _cell(rows, scen, mode)
            mq = max((r["max_queue"] for r in rs), default=None)
            out.append(f"| {scen} | {LABEL[mode]} | {len(valid)} | {_ms([r['avg_wait_s'] for r in valid])} | "
                       f"{_ms([r['exits_per_min'] for r in valid])} | {mq if mq is not None else '-'} | "
                       f"{_status(mode, scen, rs, valid, invalid)} |")
    out += ["", "## Relative to fixed timers (avg wait)", "", *summary(rows), "",
            "## Details (incl. surge, trip delay, saturation)", "",
            "| demand | controller | n | trip delay (s) | max ext. queue | deadlocks | status |",
            "|---|---|---|---|---|---|---|"]
    for scen in SCENARIOS:
        for mode in MODES:
            rs, valid, invalid = _cell(rows, scen, mode)
            if not rs and (mode != "ai" or scen == "surge"):
                status = "NOT RUN (not in this experiment)" if scen == "surge" and mode == "ai" else _status(mode, scen, rs, valid, invalid)
            else:
                status = _status(mode, scen, rs, valid, invalid)
            out.append(f"| {scen} | {LABEL[mode]} | {len(valid)} | {_ms([r['trip_delay_s'] for r in valid])} | "
                       f"{max((r['max_external_queue'] for r in rs), default='-')} | "
                       f"{sum(r['deadlocks'] for r in rs) if rs else '-'} | {status} |")
    if ai_rows:
        out += ["", "## Gemini runs", "",
                "| demand | seed | model | calls ok / failed | avg wait (s) | max_pressure same seed (s) | Δ vs max_pressure | status |",
                "|---|---|---|---|---|---|---|---|"]
        for r in sorted(ai_rows, key=lambda r: (SCENARIOS.index(r["scenario"]), r["seed"])):
            mp = next((x for x in rows if x["mode"] == "max_pressure" and x["scenario"] == r["scenario"]
                       and x["seed"] == r["seed"]), None)
            delta = f"{r['avg_wait_s'] - mp['avg_wait_s']:+.1f}" if mp and r["status"] == "filled" else "-"
            st = r["status"] + (f" (limit at t={r['limit_reached_at_t']:.0f} s)" if r["status"] == "INVALID" else "")
            out.append(f"| {r['scenario']} | {r['seed']} | {r.get('model', '?')} | {r.get('ai_calls_ok')} / "
                       f"{r.get('ai_calls_failed')} | {r['avg_wait_s']} | {mp['avg_wait_s'] if mp else '-'} | {delta} | {st} |")
    out += ["",
            "## Optional baseline: existing OSM signals + fixed timing",
            "",
            "NOT RUN: not applicable on the demo city (a synthetic grid has no OSM signals).",
            "",
            "## Caveats",
            "",
            "- n = 3 seeds; differences smaller than the sd are not meaningful.",
            "- Max-pressure's hysteresis (10 vehicles) and Webster's saturation flow (900 veh/h/lane) were tuned on seed 1 "
            "with all 9 junctions signalised; seed 1 is not out-of-sample.",
            "- Demand is simulated (no real traffic data); results are relative to this model, not measurements.",
            f"- Rows: {meta.get('runs', '?')}; wall time of the last invocation {meta.get('wall_s', '?')} s.",
            ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-ai", action="store_true", help="no Gemini runs (rows stay USER MUST RUN)")
    ap.add_argument("--only-ai", action="store_true", help="only run the Gemini rows, keep the others from results.json")
    ap.add_argument("--ai-pace", type=int, default=10, help="max Gemini calls per minute")
    ap.add_argument("--ai-interval", type=float, default=20.0, help="sim-seconds between Gemini calls")
    ap.add_argument("--ai-seeds", type=int, nargs="+", default=AI_SEEDS)
    ap.add_argument("--ai-demands", nargs="+", default=AI_DEMANDS, choices=SCENARIOS)
    ap.add_argument("--seeds", type=int, nargs="+", default=SEEDS)
    ap.add_argument("--scenarios", nargs="+", default=SCENARIOS, choices=SCENARIOS)
    ap.add_argument("--duration", type=float, default=DURATION_S)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--out", type=Path, default=DOCS / "results.md")
    ap.add_argument("--render-only", action="store_true", help="re-render results.md from results.json")
    ap.add_argument("--fresh", action="store_true", help="discard previous rows in results.json")
    args = ap.parse_args(argv)
    json_path = args.out.with_suffix(".json")
    if args.render_only:
        data = json.loads(json_path.read_text("utf-8"))
        args.out.write_bytes(render(data["rows"], data["meta"]).encode("utf-8"))
        print(f"rendered {args.out} from {len(data['rows'])} rows")
        return 0

    jobs = []
    if not args.only_ai:
        jobs += [{"scenario": s, "mode": m, "seed": seed, "duration": args.duration}
                 for s in args.scenarios for m in ("fixed", "webster", "max_pressure") for seed in args.seeds]
    if not args.skip_ai:
        jobs += [{"scenario": s, "mode": "ai", "seed": seed, "duration": args.duration, "ai_pace": args.ai_pace,
                  "ai_interval": args.ai_interval} for s in args.ai_demands for seed in args.ai_seeds]

    old = [] if args.fresh or not json_path.exists() else json.loads(json_path.read_text("utf-8"))["rows"]
    t0 = time.perf_counter()
    rows: list[dict] = []

    def save() -> None:  # after every row: a crash or quota stop never loses finished runs
        keys = {(r["scenario"], r["mode"], r["seed"]) for r in rows}
        merged = [r for r in old if (r["scenario"], r["mode"], r["seed"]) not in keys] + rows
        meta = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "runs": len(merged),
                "wall_s": round(time.perf_counter() - t0, 1)}
        json_path.write_bytes((json.dumps({"meta": meta, "rows": merged}, indent=1) + "\n").encode("utf-8"))
        args.out.write_bytes(render(merged, meta).encode("utf-8"))

    plain = [j for j in jobs if j["mode"] != "ai"]
    ai_jobs = [j for j in jobs if j["mode"] == "ai"]
    if plain:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for row in pool.map(run_one, plain):
                rows.append(row)
                print(f"{row['scenario']:7} {row['mode']:13} seed={row['seed']} wait={row['avg_wait_s']:7.1f} "
                      f"thr={row['exits_per_min']:5.1f}/min maxq={row['max_queue']:3d} ext={row['max_external_queue']:4d} "
                      f"dead={row['deadlocks']} ({row['wall_s']} s)", flush=True)
        save()
    for j in ai_jobs:  # sequential: shares one API budget / rate limit
        row = run_one(j)
        rows.append(row)
        save()
        print(f"{row['scenario']:7} gemini        seed={row['seed']} wait={row['avg_wait_s']:7.1f} "
              f"thr={row['exits_per_min']:5.1f}/min maxq={row['max_queue']:3d} status={row['status']} "
              f"limit_at={row.get('limit_reached_at_t')} calls ok/failed={row.get('ai_calls_ok')}/"
              f"{row.get('ai_calls_failed')} ({row['wall_s']} s)", flush=True)
    print(f"wrote {args.out} and {json_path} ({time.perf_counter() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
