"""Entry point for the optional SignalFlow.exe (PyInstaller). Not used by start.bat.

Runs the real backend + built frontend on http://127.0.0.1:8000 and opens the browser. Reads .env
from the folder next to the exe and keeps runtime state (usage counter, caches) in ./state there.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import threading
import webbrowser
from pathlib import Path


def main() -> None:
    multiprocessing.freeze_support()  # siting refine uses a process pool
    home = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    env_file = home / ".env"
    if env_file.exists():
        for raw in env_file.read_text("utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    os.environ.setdefault("STATE_DIR", str(home / "state"))

    import uvicorn

    from backend.app import create_app

    port = int(os.environ.get("PORT", "8000"))
    threading.Timer(2.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"SignalFlow: http://127.0.0.1:{port}  (close this window to stop)", flush=True)
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
