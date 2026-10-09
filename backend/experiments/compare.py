"""Controller comparison on the demo city -> docs/results.md (+ docs/results.json).

    python -m backend.experiments.compare --skip-ai                 # fixed / webster / max_pressure, 3 seeds
    python -m backend.experiments.compare --only-ai --ai-pace 10    # add the AI rows (needs GEMINI_API_KEY)

Scenarios: low, medium, high, rush, and surge (rush, then at t=SURGE_AT_S a 2x entry override on
SURGE_ENTRY via a demand segment). Every intersection of the demo city is signalised in every mode
(same selected set). 10 simulated minutes per run; metrics are the contract metrics at the end.

AI runs: the supervisor is scheduled on SIM time (default every 20 sim-s) and by default only
1 seed x 2 demands (medium, rush). If a run hits the AI limit / fallback, limit_reached_at_t is
recorded and the row is INVALID (never averaged into AI results). The LLM is non-deterministic:
n and sd are reported. Rows accumulate in docs/results.json, so AI rows can be added later.
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
SURGE_AT_S = 300.0
SURGE_ENTRY = "n20"  # west end of the primary street
SURGE_FACTOR = 2.0
DURATION_S = 600.0
SEEDS = [1, 2, 3]
AI_DEMANDS = ["medium", "rush"]
AI_SEEDS = [1]


def _area():
    from backend.contract.models import RoadNetwork
    from backend.roadnet.intersections import build_intersections
    from backend.siting.prefilter import rank

    net = RoadNetwork.model_validate_json((ROOT / "backend" / "data" / "grid_network.json").read_text("utf-8"))
    ranked, _ = rank(net, build_intersections(net))
    return net, ranked


def run_one(job: dict) -> dict:
    """One (scenario, mode, seed) run. Top-level so it can run in a worker process."""
    from backend.control.runner import run_headless
    from backend.sim import SimEngine
    from backend.traffic.demand import build_profile

    net, ixs = _area()
    scenario, mode, seed = job["scenario"], job["mode"], job["seed"]
    level = "rush" if scenario == "surge" else scenario
    profile = build_profile(area_id="area_grid_mock", entry_nodes=list(net.entry_nodes), level=level,
                            profile_id=f"dp_exp_{level}")
    selected = [i.id for i in ixs] if not job.get("osm_only") else [i.id for i in ixs if i.has_signal_in_osm]
    engine = SimEngine(net, ixs, seed, profile, selected)
    if scenario == "surge":
        engine.set_demand(entry_overrides={SURGE_ENTRY: SURGE_FACTOR}, at_t=SURGE_AT_S)
    controller = None
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
    res = run_headless(engine, mode, job.get("duration", DURATION_S), controller=controller,
                       ai_interval_s=job.get("ai_interval", 20.0))
    m = res.metrics
    row = {
        "scenario": scenario, "mode": mode, "seed": seed, "duration_s": job.get("duration", DURATION_S),
        "avg_wait_s": round(m.avg_wait_s, 2), "trip_delay_s": round(m.trip_delay_s, 2),
        "avg_queue": round(m.avg_queue, 2), "max_avg_queue": round(res.max_avg_queue, 2),
        "throughput_per_min": m.throughput_per_min, "external_queue_end": sum(map(len, engine.external.values())),
        "max_external_queue": res.max_external, "deadlocks": m.deadlocks, "blocked_spawns": m.blocked_spawns,
        "generated": engine.generated, "safety_overrides": res.overrides, "wall_s": round(time.perf_counter() - t0, 1),
        "status": "filled",
    }
    if mode == "ai":
        row.update(ai_calls_ok=res.ai_calls_ok, ai_calls_failed=res.ai_calls_failed,
                   limit_reached_at_t=res.limit_reached_at_t)
        if res.limit_reached_at_t is not None:
            row["status"] = "INVALID"
    return row


def _fmt(values: list[float]) -> str:
    if not values:
        return "-"
    if len(values) == 1:
        return f"{values[0]:.1f}"
    return f"{statistics.mean(values):.1f} ± {statistics.stdev(values):.1f}"


def summary(rows: list[dict]) -> list[str]:
    lines = []
    for scen in SCENARIOS:
        means = {}
        for mode in MODES:
            vals = [r["avg_wait_s"] for r in rows if r["scenario"] == scen and r["mode"] == mode and r["status"] == "filled"]
            if vals:
                means[mode] = statistics.mean(vals)
        if "fixed" not in means:
            continue
        parts = [f"{m} {v:.1f} s ({(v - means['fixed']) / means['fixed'] * 100:+.0f}% vs fixed)"
                 for m, v in means.items() if m != "fixed"]
        lines.append(f"- **{scen}**: fixed {means['fixed']:.1f} s; " + "; ".join(parts))
    return lines


def render(rows: list[dict], meta: dict) -> str:
    out = [
        "# Results: controller comparison (demo city)",
        "",
        f"_Generated {meta['generated_at']} by `python -m backend.experiments.compare`. Raw rows: `docs/results.json`._",
        "",
        "Setup: synthetic 3×3 demo grid (`area_grid_mock`), **all 9 junctions signalised in every mode**, "
        f"{int(DURATION_S / 60)} simulated minutes per run, contract metrics at the end of the run "
        "(population = trips finished in the last 120 s + vehicles in the network + external spawn queue). "
        "Demand is simulated: per-entry flow low 120 / medium 200 / high 280 / rush 380 veh/h; "
        f"**surge** = rush, then from t={SURGE_AT_S:.0f} s a {SURGE_FACTOR:g}× override on entry `{SURGE_ENTRY}` "
        "(west end of the primary street) via a demand segment. Same seed ⇒ identical demand schedule "
        "across controllers. Values are mean ± sd over n seeds.",
        "",
        "Row status: **filled** = measured here · **INVALID** = AI run hit the limit/fallback (excluded from "
        "means; `limit_reached_at_t` recorded) · **USER MUST RUN** = needs Gemini access (run locally).",
        "",
    ]
    for scen in SCENARIOS:
        out += [f"## {scen}", "",
                "| mode | n | avg_wait_s | trip_delay_s | max avg_queue | max ext. queue | deadlocks | status |",
                "|---|---|---|---|---|---|---|---|"]
        for mode in MODES:
            rs = [r for r in rows if r["scenario"] == scen and r["mode"] == mode]
            valid = [r for r in rs if r["status"] == "filled"]
            invalid = [r for r in rs if r["status"] == "INVALID"]
            if not rs:
                status = "USER MUST RUN" if mode == "ai" and scen in AI_DEMANDS else (
                    f"USER MUST RUN (optional: `--only-ai --ai-demands {scen}`)" if mode == "ai" else "NOT RUN")
                out.append(f"| {mode} | 0 | - | - | - | - | - | {status} |")
                continue
            status = "filled" if valid else "INVALID"
            if invalid:
                status += f" ({len(invalid)} INVALID: limit at t=" + \
                          ", ".join(f"{r['limit_reached_at_t']:.0f}" for r in invalid) + " s)"
            out.append(
                f"| {mode} | {len(valid)} | {_fmt([r['avg_wait_s'] for r in valid])} | "
                f"{_fmt([r['trip_delay_s'] for r in valid])} | "
                f"{max(r['max_avg_queue'] for r in rs):.1f} | {max(r['max_external_queue'] for r in rs)} | "
                f"{sum(r['deadlocks'] for r in rs)} | {status} |")
        out.append("")
    # ablation
    out += ["## Ablation: max_pressure vs ai (same seeds and demands)", "",
            "| scenario | seed | max_pressure avg_wait_s | ai avg_wait_s | Δ (ai − mp) | ai status |", "|---|---|---|---|---|---|"]
    any_ai = False
    for r in sorted((r for r in rows if r["mode"] == "ai"), key=lambda r: (SCENARIOS.index(r["scenario"]), r["seed"])):
        mp = next((x for x in rows if x["mode"] == "max_pressure" and x["scenario"] == r["scenario"]
                   and x["seed"] == r["seed"]), None)
        any_ai = True
        delta = f"{r['avg_wait_s'] - mp['avg_wait_s']:+.1f}" if mp and r["status"] == "filled" else "-"
        out.append(f"| {r['scenario']} | {r['seed']} | {mp['avg_wait_s'] if mp else '-'} | {r['avg_wait_s']} | {delta} | {r['status']} |")
    if not any_ai:
        out.append("| medium, rush | 1 | see above | - | - | USER MUST RUN |")
    out += ["",
            "The LLM is non-deterministic: repeated AI runs with the same seed differ. Report n and sd; "
            "do not read a single AI run as a result.",
            "",
            "## Optional baseline: existing OSM signals + fixed timing",
            "",
            "NOT RUN: not applicable on the demo city (a synthetic grid has no OSM signals), and compare.py "
            "only runs the demo city so far. Extending it to an OSM sample is future work.",
            "",
            "## Summary (computed from the rows above)",
            "",
            *summary(rows),
            "",
            "Caveats: the max-pressure hysteresis (10 vehicles) and Webster's saturation flow (900) were tuned "
            "on seed 1 before these runs (see the controller docstrings), so seed 1 is not out-of-sample. "
            "Differences smaller than the sd are not meaningful with n = 3.",
            "",
            "## Saturation check (C8)",
            "",
            "`max avg_queue` = largest network-mean stop-line queue sampled every 10 s; `max ext. queue` = largest "
            "number of vehicles waiting outside the network to enter. Rush/surge should show growing queues "
            "and a non-empty external queue, with **0 deadlocks** (no gridlock).",
            "",
            "## Notes",
            "",
            f"- Runs: {meta.get('runs', '?')} rows; wall time of the last invocation {meta.get('wall_s', '?')} s.",
            "- Max-pressure uses a switch hysteresis of 10 vehicles; Webster uses 900 veh/h/lane saturation flow "
            "and a 90 s cycle cap, both calibrated for this engine (see their module docstrings).",
            "- All controllers pass through the same safety layer (min green, transition lock, max-red guard); "
            "override counts are in `docs/results.json`.",
            ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-ai", action="store_true", help="no AI runs (AI rows stay USER MUST RUN)")
    ap.add_argument("--only-ai", action="store_true", help="only run the AI rows, keep the others from results.json")
    ap.add_argument("--ai-pace", type=int, default=None, help="max Gemini calls per minute (free tier: ~10)")
    ap.add_argument("--ai-interval", type=float, default=20.0, help="sim-seconds between AI calls (headless)")
    ap.add_argument("--ai-seeds", type=int, nargs="+", default=AI_SEEDS)
    ap.add_argument("--ai-demands", nargs="+", default=AI_DEMANDS, choices=SCENARIOS)
    ap.add_argument("--seeds", type=int, nargs="+", default=SEEDS)
    ap.add_argument("--scenarios", nargs="+", default=SCENARIOS, choices=SCENARIOS)
    ap.add_argument("--duration", type=float, default=DURATION_S)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--osm-baseline", action="store_true", help="(OSM areas) also run existing-OSM-signals + fixed")
    ap.add_argument("--out", type=Path, default=DOCS / "results.md")
    ap.add_argument("--render-only", action="store_true", help="re-render results.md from results.json")
    args = ap.parse_args(argv)
    if args.render_only:
        data = json.loads(args.out.with_suffix(".json").read_text("utf-8"))
        args.out.write_bytes(render(data["rows"], data["meta"]).encode("utf-8"))
        print(f"rendered {args.out} from {len(data['rows'])} rows")
        return 0
    if args.osm_baseline:
        _, ixs = _area()
        if not any(i.has_signal_in_osm for i in ixs):
            print("--osm-baseline: not applicable on the demo city (no OSM signals); skipped")

    jobs = []
    if not args.only_ai:
        jobs += [{"scenario": s, "mode": m, "seed": seed, "duration": args.duration}
                 for s in args.scenarios for m in ("fixed", "webster", "max_pressure") for seed in args.seeds]
    if not args.skip_ai:
        jobs += [{"scenario": s, "mode": "ai", "seed": seed, "duration": args.duration, "ai_pace": args.ai_pace,
                  "ai_interval": args.ai_interval} for s in args.ai_demands for seed in args.ai_seeds]

    json_path = args.out.with_suffix(".json")
    old = json.loads(json_path.read_text("utf-8"))["rows"] if json_path.exists() else []
    t0 = time.perf_counter()
    plain = [j for j in jobs if j["mode"] != "ai"]
    ai_jobs = [j for j in jobs if j["mode"] == "ai"]
    rows: list[dict] = []
    if plain:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for row in pool.map(run_one, plain):
                rows.append(row)
                print(f"{row['scenario']:7} {row['mode']:13} seed={row['seed']} wait={row['avg_wait_s']:7.1f} "
                      f"delay={row['trip_delay_s']:7.1f} maxq={row['max_avg_queue']:4.1f} "
                      f"ext={row['max_external_queue']:4d} dead={row['deadlocks']} ({row['wall_s']} s)", flush=True)
    for j in ai_jobs:  # sequential: shares one API budget / rate limit
        row = run_one(j)
        rows.append(row)
        print(f"{row['scenario']:7} ai            seed={row['seed']} wait={row['avg_wait_s']:7.1f} "
              f"status={row['status']} limit_at={row.get('limit_reached_at_t')} calls ok/failed="
              f"{row.get('ai_calls_ok')}/{row.get('ai_calls_failed')}", flush=True)

    keys = {(r["scenario"], r["mode"], r["seed"]) for r in rows}
    merged = [r for r in old if (r["scenario"], r["mode"], r["seed"]) not in keys] + rows
    meta = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "runs": len(merged),
            "wall_s": round(time.perf_counter() - t0, 1)}
    json_path.write_bytes((json.dumps({"meta": meta, "rows": merged}, indent=1) + "\n").encode("utf-8"))
    args.out.write_bytes(render(merged, meta).encode("utf-8"))
    print(f"wrote {args.out} and {json_path} ({len(merged)} rows, {meta['wall_s']} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
