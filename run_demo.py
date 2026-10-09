"""Run the demo: backend + built frontend on ONE port (http://127.0.0.1:8000). Ctrl+C stops it.

    python run_demo.py                 # real backend + real simulation, opens the browser
    python run_demo.py --mock          # mock backend (canned simulation)
    python run_demo.py --dev           # Vite dev server on :5173 + backend on :8000 (hot reload)
    python run_demo.py --check         # headless self-test, exits 0/1 (see run_check)
    python run_demo.py --scenario ai_limit --mock    # mock scenarios: ai_limit, ai_replay

The frontend is (re)built with `npm run build` when frontend/dist is missing or older than the
sources (needs Node.js 20+; skipped if npm is not installed but a build exists).
AI mode needs GEMINI_API_KEY in .env; GEMINI_FAKE_FAIL=limit simulates the AI limit offline.
Works on Windows, macOS and Linux (start.bat wraps it on Windows).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
IS_WIN = os.name == "nt"


class _Done(Exception):
    """Check finished: leave the try block so the finally clause stops the server, then exit."""


def say(msg: str) -> None:
    print(f"[run_demo] {msg}", flush=True)


def die(msg: str, code: int = 1) -> None:
    say(msg)
    sys.exit(code)


def spawn(cmd: list[str], cwd: Path, env: dict[str, str], quiet: bool = False) -> subprocess.Popen:
    kwargs: dict = {"cwd": cwd, "env": env}
    if quiet:
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def kill_tree(p: subprocess.Popen) -> None:
    if p.poll() is not None:
        return
    if IS_WIN:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def http(url: str, body: dict | None = None, timeout: float = 15) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def wait_http(url: str, timeout_s: float = 30) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def frontend_stale() -> bool:
    index = DIST / "index.html"
    if not index.exists():
        return True
    built = index.stat().st_mtime
    sources = [FRONTEND / "index.html", FRONTEND / "package.json", *FRONTEND.joinpath("src").rglob("*")]
    return any(p.is_file() and p.stat().st_mtime > built for p in sources)


def ensure_frontend(force: bool = False) -> None:
    if not force and not frontend_stale():
        return
    npm = shutil.which("npm")
    if not npm:
        if (DIST / "index.html").exists():
            say("npm not found; using the existing frontend build")
            return
        die("frontend is not built and npm was not found: install Node.js 20+, or run `npm ci && npm run build` in frontend/")
    if not (FRONTEND / "node_modules").exists():
        say("installing frontend dependencies (npm ci)...")
        subprocess.run([npm, "ci"], cwd=FRONTEND, check=True)
    say("building the frontend (npm run build)...")
    subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True, stdout=subprocess.DEVNULL)


# ------------------------------------------------------------------ headless check
async def _watch(base: str, sid: str, seconds: float, out: dict) -> None:
    import websockets  # installed with backend/requirements.txt

    ws_url = base.replace("http", "ws", 1) + f"/ws/sim?session_id={sid}"
    ticks, max_gap, last = [], 0.0, time.monotonic()
    async with websockets.connect(ws_url) as w:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            raw = await asyncio.wait_for(w.recv(), timeout=5)  # a 5 s silence = frozen stream
            now = time.monotonic()
            max_gap, last = max(max_gap, now - last), now
            ticks.append(json.loads(raw))
    out[sid] = {"ticks": ticks, "max_gap_s": max_gap}


def run_check(base: str, mock: bool, scenario: str | None, seconds: float) -> list[str]:
    """Self-test against a running server. Returns a list of failures (empty = pass)."""
    fails: list[str] = []

    def check(cond: bool, msg: str) -> None:
        say(("PASS " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    st, body = http(base + "/")
    check(st == 200 and b'<div id="root">' in body, "GET / serves the built frontend on the same port")
    st, body = http(base + "/api/health")
    check(st == 200, f"GET /api/health ({json.loads(body).get('contract_version') if st == 200 else st})")
    st, body = http(base + "/api/demo-area")
    area = json.loads(body) if st == 200 else {}
    check(st == 200 and area.get("source") == "synthetic_grid" and len(area.get("recommended_ids", [])) == 3,
          "demo city loads with the top 3 junctions preselected")
    if not area:
        return fails
    st, body = http(base + "/api/demand/resolve", {"area_id": area["area_id"], "level": "rush"})
    profile = json.loads(body).get("demand_profile", {}) if st == 200 else {}
    check(st == 200 and bool(profile), "simulated demand resolves (rush)")
    sids: dict[str, str] = {}
    q = f"?scenario={scenario}" if (mock and scenario) else ""
    for mode in ("fixed", "ai"):
        st, body = http(base + "/api/sim/start" + q, {"area_id": area["area_id"], "mode": mode,
                        "demand_profile_id": profile.get("id"), "seed": 42, "selected_intersections": area["recommended_ids"],
                        "speed": 4})
        check(st == 200, f"start {mode} session")
        if st == 200:
            sids[mode] = json.loads(body)["session_id"]
    if len(sids) < 2:
        return fails
    time.sleep(2)
    st0, b0 = http(base + f"/api/metrics?session_id={sids['fixed']}")
    t_now = json.loads(b0)["t"] if st0 == 200 else 0
    at_t = int(t_now) + 20
    codes = [http(base + "/api/sim/demand", {"session_id": s, "multiplier": 2.0, "at_t": at_t})[0] for s in sids.values()]
    check(codes == [200, 200], f"Surge: POST /api/sim/demand to both sessions with the same at_t={at_t}")
    results: dict = {}

    async def watch_all() -> None:
        await asyncio.gather(*(_watch(base, s, seconds, results) for s in sids.values()))

    try:
        asyncio.run(watch_all())
    except Exception as e:  # noqa: BLE001 - report any stream failure as a check failure
        check(False, f"WebSocket streams ({type(e).__name__}: {e})")
        return fails
    for mode, sid in sids.items():
        r = results[sid]
        ts = [t["t"] for t in r["ticks"]]
        check(len(ts) > seconds * 2 and ts[-1] > ts[0] + 10, f"{mode}: ticks keep coming and sim time advances "
              f"({len(ts)} ticks, t {ts[0]:.0f} -> {ts[-1]:.0f} s)")
        check(r["max_gap_s"] < 3.0, f"{mode}: never freezes (largest gap between ticks {r['max_gap_s']:.2f} s)")
        notes = [e["text"] for t in r["ticks"] for e in t["explanations"]]
        check(any("Demand changed" in n for n in notes), f"{mode}: surge reached the stream")
    ai_last = results[sids["ai"]]["ticks"][-1]
    cs = ai_last["controller_status"]
    expect_limit = (os.environ.get("GEMINI_FAKE_FAIL", "").lower() in ("limit", "429")) or (mock and scenario == "ai_limit")
    if expect_limit:
        check(cs["state"] == "ai_limit_reached" and cs["effective_controller"] == "max_pressure",
              f"AI limit: state {cs['state']}, adaptive fallback ({cs['effective_controller']})")
        check(cs["message"] == "Live AI quota reached. Using adaptive fallback.",
              f"AI limit banner text: '{cs['message']}'")
        check(bool(ai_last["signals"]), "AI session still shows live signals (adaptive fallback)")
    else:
        say(f"INFO AI session state: {cs['state']} ({cs['message']})")
    for sid in sids.values():
        http(base + "/api/sim/stop", {"session_id": sid})
    return fails


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="mock backend instead of the real simulation")
    ap.add_argument("--dev", action="store_true", help="Vite dev server (:5173) + backend (:8000)")
    ap.add_argument("--check", action="store_true", help="headless self-test, then exit 0/1")
    ap.add_argument("--check-seconds", type=float, default=25.0)
    ap.add_argument("--scenario", choices=["none", "ai_limit", "ai_replay"], default=None, help="mock scenario")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="force npm run build")
    args = ap.parse_args()

    if sys.version_info < (3, 11):
        die(f"Python 3.11+ required, found {sys.version.split()[0]}")
    try:
        import fastapi  # noqa: F401
        import numpy  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        die("backend deps missing: python -m pip install -r backend/requirements.txt")

    def _interrupt(*_: object) -> None:  # Ctrl+Break (Windows) / SIGTERM behave like Ctrl+C
        raise KeyboardInterrupt

    signal.signal(signal.SIGBREAK if IS_WIN else signal.SIGTERM, _interrupt)

    env = dict(os.environ)
    if args.scenario:
        env["MOCK_SCENARIO"] = args.scenario
    if args.check and "GEMINI_FAKE_FAIL" not in env and not args.mock:
        env["GEMINI_FAKE_FAIL"] = "limit"  # the self-test never calls the real API
        os.environ["GEMINI_FAKE_FAIL"] = "limit"
        say("check mode: GEMINI_FAKE_FAIL=limit (no real Gemini calls)")
    env["BACKEND_URL"] = f"http://127.0.0.1:{args.port}"
    if not args.dev:
        ensure_frontend(force=args.rebuild)

    target, label = ("backend.mock_server:app", "mock backend") if args.mock else ("backend.app:app", "real backend")
    procs: list[subprocess.Popen] = []
    code = 0
    try:
        procs.append(spawn([sys.executable, "-m", "uvicorn", target, "--host", "127.0.0.1", "--port", str(args.port),
                            "--log-level", "warning"], ROOT, env, quiet=args.check))
        base = f"http://127.0.0.1:{args.port}"
        if not wait_http(base + "/api/health"):
            raise SystemExit(f"[run_demo] {label} did not start (is port {args.port} in use?)")
        if args.check:
            fails = run_check(base, args.mock, args.scenario, args.check_seconds)
            say("CHECK PASSED" if not fails else f"CHECK FAILED ({len(fails)}): " + "; ".join(fails))
            code = 0 if not fails else 1
            raise _Done
        url = base
        if args.dev:
            npm = shutil.which("npm") or die("npm not found; install Node.js 20+")
            procs.append(spawn([npm, "run", "dev"], FRONTEND, env))
            url = "http://localhost:5173"
        say(f"{label} + frontend: {url}   (Ctrl+C to stop)")
        if not args.no_browser:
            webbrowser.open(url)
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        say("a child process exited; shutting down")
        code = 1
    except _Done:
        pass
    except KeyboardInterrupt:
        say("stopping...")
    finally:
        for p in reversed(procs):
            kill_tree(p)
    sys.exit(code)


if __name__ == "__main__":
    main()
