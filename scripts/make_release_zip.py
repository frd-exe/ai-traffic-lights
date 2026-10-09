"""Pack the release ZIP: release/SignalFlow-release.zip

Contents: every git-tracked file (source, start.bat, run_demo.py, README.md, docs/results.md, ...)
+ the built frontend (frontend/dist) + .env copied from the local .env (shared Gemini key)
+ release/exe/SignalFlow/** if build_release.py produced the optional exe.
Never included: the Gemini call counter (usage.json) and any other runtime state
(backend/data/state/**), caches, node_modules, .venv, .git.
The ZIP contains your API key: share it only with the people who should use that key.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "release" / "SignalFlow-release.zip"
TOP = "SignalFlow"  # folder name inside the ZIP
EXCLUDED_PARTS = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", "test-results", "playwright-report"}
REQUIRED = ["README.md", "docs/results.md", "start.bat", "run_demo.py", "frontend/dist/index.html", ".env"]


def excluded(rel: str) -> bool:
    parts = Path(rel).parts
    return (rel.startswith("backend/data/state/") or rel.startswith("backend/data/cache/") or rel.startswith("release/")
            or Path(rel).name == "usage.json" or any(p in EXCLUDED_PARTS for p in parts))


def main() -> int:
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.decode().split("\0")
    files = {f for f in tracked if f and (ROOT / f).is_file() and not excluded(f)}
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").exists():
        raise SystemExit("frontend/dist missing: run python scripts/build_release.py first")
    files |= {p.relative_to(ROOT).as_posix() for p in dist.rglob("*") if p.is_file()}
    if not (ROOT / ".env").exists():
        raise SystemExit("no local .env to copy into the release")
    files.add(".env")
    exe_dir = ROOT / "release" / "exe" / "SignalFlow"
    exe_files = [p for p in exe_dir.rglob("*") if p.is_file()] if exe_dir.exists() else []
    missing = [r for r in REQUIRED if r not in files]
    if missing:
        raise SystemExit(f"release is missing: {missing}")
    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel in sorted(files):
            z.write(ROOT / rel, f"{TOP}/{rel}")
        for p in exe_files:
            z.write(p, f"{TOP}/exe/{p.relative_to(exe_dir).as_posix()}")
        if exe_files and (ROOT / ".env").exists():
            z.write(ROOT / ".env", f"{TOP}/exe/.env")  # the exe reads .env next to itself
    with zipfile.ZipFile(OUT) as z:
        names = z.namelist()
    leaked = [n for n in names if n.endswith("usage.json") or "/backend/data/state/" in n]
    if leaked:
        OUT.unlink()
        raise SystemExit(f"refusing to ship runtime state: {leaked}")
    size = OUT.stat().st_size / 1e6
    print(f"[make_release_zip] {len(names)} files, {size:.1f} MB, exe={'yes' if exe_files else 'no'} -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
