"""Serve the built frontend (frontend/dist) from the backend, so the demo runs on ONE port.

Mounted after all /api and /ws routes, so those keep priority. The app is a single page with no
client-side routes, so no index.html fallback is needed; unknown paths get the usual 404.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
log = logging.getLogger("backend.static")


def mount_frontend(app: FastAPI, dist: Path = DIST) -> bool:
    if not (dist / "index.html").exists():
        log.info("frontend/dist not built; serving the API only (npm run build in frontend/)")
        return False
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return True
