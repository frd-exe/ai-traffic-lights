"""POST /api/area: network + ranked intersections, computed once per area_id and cached.

Sources:
 - synthetic_grid: backend/data/grid_network.json under GRID_AREA_ID. Used for `demo_city`,
   GET /api/demo-area, and whenever no OSM sample exists (we never call Overpass from the server).
 - osm: backend/data/sample_area.json (written by scripts/fetch_sample_area.py), clipped to the bbox.

Cache: memory -> disk (<state>/areas/<area_id>.json) -> compute. Disk entries make areas work
offline and survive restarts. Only computations count against the per-client rate limit.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Literal

from backend.api_common import ApiError
from backend.contract.constants import AREA_RATE_LIMIT_PER_MIN, GRID_AREA_ID, MAX_BBOX_SIDE_M
from backend.contract.helpers import area_id as make_area_id
from backend.contract.models import AreaRequest, AreaResponse, BBox, RoadNetwork
from backend.roadnet.geo import bbox_side_lengths_m
from backend.roadnet.intersections import build_intersections
from backend.roadnet.overpass import OverpassParseError, parse_overpass
from backend.siting.prefilter import rank

log = logging.getLogger(__name__)
CacheHit = Literal["memory", "disk", "computed"]


class RateLimiter:
    def __init__(self, limit: int, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window_s, self.clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = self.clock()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                retry = int(self.window_s - (now - q[0])) + 1
                raise ApiError(429, "rate_limited",
                               f"too many new areas; try again in {retry} s (cached areas are not limited)",
                               {"retry_after_s": retry})
            q.append(now)


def _bboxes_overlap(a: BBox, b: BBox) -> bool:
    return a.west < b.east and b.west < a.east and a.south < b.north and b.south < a.north


class AreaService:
    def __init__(self, grid_path: Path, sample_path: Path, cache_dir: Path,
                 rate_limit_per_min: int = AREA_RATE_LIMIT_PER_MIN):
        self.grid_path, self.sample_path, self.cache_dir = grid_path, sample_path, cache_dir
        self.limiter = RateLimiter(rate_limit_per_min)
        self._mem: dict[str, AreaResponse] = {}
        self._lock = threading.Lock()
        self._sample: dict | None = None
        self._sample_mtime: float | None = None

    # ---------------------------------------------------------------- cache
    def _disk_path(self, aid: str) -> Path:
        return self.cache_dir / f"{aid}.json"

    def _store(self, area: AreaResponse) -> None:
        self._mem[area.area_id] = area
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        p = self._disk_path(area.area_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(area.model_dump_json().encode("utf-8"))
        os.replace(tmp, p)

    def _lookup(self, aid: str) -> tuple[AreaResponse, CacheHit] | None:
        with self._lock:
            if aid in self._mem:
                return self._mem[aid], "memory"
            p = self._disk_path(aid)
            if p.exists():
                try:
                    area = AreaResponse.model_validate_json(p.read_text("utf-8"))
                except ValueError:
                    log.warning("ignoring corrupt area cache %s", p)
                    return None
                self._mem[aid] = area
                return area, "disk"
        return None

    def get(self, aid: str) -> AreaResponse:
        hit = self._lookup(aid)
        if hit is None and aid == GRID_AREA_ID:
            return self.demo_area()[0]
        if hit is None:
            raise ApiError(404, "unknown_area", f"area {aid} not found; POST /api/area first")
        return hit[0]

    def entry_nodes(self, aid: str) -> list[str]:
        return list(self.get(aid).network.entry_nodes)

    # ---------------------------------------------------------------- sources
    def demo_area(self) -> tuple[AreaResponse, CacheHit]:
        hit = self._lookup(GRID_AREA_ID)
        if hit:
            return hit
        net = RoadNetwork.model_validate_json(self.grid_path.read_text("utf-8"))
        ranked, recommended = rank(net, build_intersections(net), ignore_osm_signals=False)
        area = AreaResponse(area_id=GRID_AREA_ID, source="synthetic_grid", network=net,
                            intersections=ranked, recommended_ids=recommended)
        with self._lock:
            self._store(area)
        return area, "computed"

    def sample_available(self) -> bool:
        return self.sample_path.exists()

    def _load_sample(self) -> dict:
        mtime = self.sample_path.stat().st_mtime
        if self._sample is None or self._sample_mtime != mtime:
            try:
                self._sample = json.loads(self.sample_path.read_text("utf-8"))
            except ValueError as e:
                raise ApiError(500, "bad_sample_area", f"{self.sample_path.name} is not valid JSON: {e}")
            self._sample_mtime = mtime
        return self._sample

    # ---------------------------------------------------------------- main
    def analyze(self, req: AreaRequest, client: str = "local") -> tuple[AreaResponse, CacheHit]:
        if req.demo_city or not self.sample_available():
            if not req.demo_city:
                log.info("no %s; serving the synthetic grid demo city", self.sample_path.name)
            return self.demo_area()

        b = req.bbox
        w, h = bbox_side_lengths_m(b.west, b.south, b.east, b.north)
        if max(w, h) > MAX_BBOX_SIDE_M:
            raise ApiError(400, "bbox_too_large",
                           f"bbox is {w:.0f} x {h:.0f} m; max side is {MAX_BBOX_SIDE_M:.0f} m. Zoom in.",
                           {"width_m": round(w), "height_m": round(h), "max_side_m": MAX_BBOX_SIDE_M})

        aid = make_area_id(b.west, b.south, b.east, b.north, req.ignore_osm_signals)
        hit = self._lookup(aid)
        if hit:
            return hit

        sample = self._load_sample()
        sbox = sample.get("bbox")
        if sbox and not _bboxes_overlap(b, BBox.model_validate(sbox)):
            raise ApiError(404, "area_not_available",
                           "no OSM data for this box. Fetch it with scripts/fetch_sample_area.py, "
                           "or use the demo city (demo_city=true / GET /api/demo-area).",
                           {"sample_bbox": sbox})
        self.limiter.check(client)
        try:
            parsed = parse_overpass(sample.get("osm", sample), bbox=b)
        except OverpassParseError as e:
            raise ApiError(422, "no_roads", f"could not build a road network here: {e}")
        ixs = build_intersections(parsed.network, exclude=parsed.roundabout_nodes, signalised=parsed.signalised_nodes)
        ranked, recommended = rank(parsed.network, ixs, ignore_osm_signals=req.ignore_osm_signals)
        area = AreaResponse(area_id=aid, source="osm", network=parsed.network,
                            intersections=ranked, recommended_ids=recommended)
        with self._lock:
            self._store(area)
        log.info("area %s computed: %s", aid, parsed.stats)
        return area, "computed"
