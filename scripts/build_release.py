"""Build release artifacts.

    python scripts/build_release.py               # frontend build + optional SignalFlow.exe
    python scripts/build_release.py --no-exe      # frontend build only

1. Frontend: `npm ci` (if needed) + `npm run build` -> frontend/dist (served by the backend).
2. Optional Windows exe (PyInstaller, one-folder) -> release/exe/SignalFlow/SignalFlow.exe.
   Skipped (not an error) if PyInstaller can't be installed, the build fails, or it exceeds
   --exe-timeout seconds; the release then ships with start.bat only.
Then run scripts/make_release_zip.py.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
RELEASE = ROOT / "release"
EXE_DIR = RELEASE / "exe"


def say(msg: str) -> None:
    print(f"[build_release] {msg}", flush=True)


def build_frontend() -> None:
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm not found: install Node.js 20+ to build the frontend")
    if not (FRONTEND / "node_modules").exists():
        say("npm ci ...")
        subprocess.run([npm, "ci"], cwd=FRONTEND, check=True)
    say("npm run build ...")
    subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True, stdout=subprocess.DEVNULL)
    if not (FRONTEND / "dist" / "index.html").exists():
        raise SystemExit("frontend build produced no dist/index.html")
    say("frontend built -> frontend/dist")


def build_exe(timeout_s: int) -> bool:
    if os.name != "nt":
        say("exe: skipped (Windows only)")
        return False
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        say("exe: installing PyInstaller ...")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pyinstaller"], capture_output=True, text=True)
        if r.returncode != 0:
            say(f"exe: SKIPPED (could not install PyInstaller: {r.stderr.strip()[-200:]})")
            return False
    sep = ";"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", "SignalFlow",
           "--distpath", str(EXE_DIR), "--workpath", str(RELEASE / "build"), "--specpath", str(RELEASE),
           "--paths", str(ROOT),
           "--add-data", f"{ROOT / 'backend' / 'data' / 'grid_network.json'}{sep}backend/data",
           "--add-data", f"{ROOT / 'backend' / 'data' / 'siting'}{sep}backend/data/siting",
           "--add-data", f"{FRONTEND / 'dist'}{sep}frontend/dist",
           "--collect-submodules", "uvicorn", "--collect-submodules", "backend",
           "--hidden-import", "networkx", "--hidden-import", "numpy",
           str(ROOT / "scripts" / "release_launcher.py")]
    say(f"exe: PyInstaller (timeout {timeout_s} s) ...")
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        say(f"exe: SKIPPED (took longer than {timeout_s} s)")
        return False
    exe = EXE_DIR / "SignalFlow" / "SignalFlow.exe"
    if r.returncode != 0 or not exe.exists():
        say(f"exe: SKIPPED (PyInstaller failed: {(r.stderr or r.stdout).strip()[-300:]})")
        return False
    say(f"exe: built -> {exe}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-exe", action="store_true")
    ap.add_argument("--exe-timeout", type=int, default=600)
    args = ap.parse_args()
    RELEASE.mkdir(exist_ok=True)
    build_frontend()
    if not args.no_exe:
        build_exe(args.exe_timeout)
    say("done; next: python scripts/make_release_zip.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
