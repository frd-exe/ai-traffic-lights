"""Start the mock backend + frontend dev server; Ctrl+C stops both cleanly.

    python run_demo.py                      # mock backend :8000, frontend :5173
    python run_demo.py --scenario ai_limit  # sets MOCK_SCENARIO for the backend
    python run_demo.py --backend-only

Works on Windows, macOS and Linux. Run it with the venv's python (or any python that has
backend/requirements.txt installed). Installs frontend deps with `npm ci` on first run.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
IS_WIN = os.name == "nt"


def die(msg: str) -> None:
    print(f"[run_demo] {msg}", file=sys.stderr)
    sys.exit(1)


def spawn(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    kwargs: dict = {"cwd": cwd, "env": env}
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


def wait_http(url: str, timeout_s: float = 20) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=["none", "ai_limit", "ai_replay", "google_down"], default=None)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--backend-only", action="store_true")
    args = ap.parse_args()

    if sys.version_info < (3, 11):
        die(f"Python 3.11+ required, found {sys.version.split()[0]}")
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        die("backend deps missing: python -m pip install -r backend/requirements.txt")

    # Treat Ctrl+Break (Windows) and SIGTERM like Ctrl+C so children are always cleaned up.
    def _interrupt(*_: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGBREAK if IS_WIN else signal.SIGTERM, _interrupt)

    env = dict(os.environ)
    if args.scenario:
        env["MOCK_SCENARIO"] = args.scenario
    env["BACKEND_URL"] = f"http://127.0.0.1:{args.port}"

    procs: list[subprocess.Popen] = []
    try:
        procs.append(spawn([sys.executable, "-m", "uvicorn", "backend.mock_server:app",
                            "--host", "127.0.0.1", "--port", str(args.port)], ROOT, env))
        if not wait_http(f"http://127.0.0.1:{args.port}/api/health"):
            die("mock backend did not start (port in use?)")
        print(f"[run_demo] mock backend: http://127.0.0.1:{args.port}/api/health")

        if not args.backend_only:
            npm = shutil.which("npm")
            if not npm:
                die("npm not found; install Node.js 20+")
            if not (FRONTEND / "node_modules").exists():
                print("[run_demo] installing frontend deps (npm ci)...")
                subprocess.run([npm, "ci"], cwd=FRONTEND, check=True)
            procs.append(spawn([npm, "run", "dev"], FRONTEND, env))
            print("[run_demo] frontend: http://localhost:5173  (Ctrl+C to stop)")

        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        die("a child process exited; shutting down")
    except KeyboardInterrupt:
        print("\n[run_demo] stopping...")
    finally:
        for p in reversed(procs):
            kill_tree(p)


if __name__ == "__main__":
    main()
