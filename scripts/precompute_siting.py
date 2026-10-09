"""Precompute simulation-based siting for an area and write the cache.

    python scripts/precompute_siting.py                    # demo area -> backend/data/siting/ (committed)
    python scripts/precompute_siting.py --area-id ar_xxx   # a cached OSM area -> backend/data/state/siting/
Options: --seed 42 --level rush --workers N
No network needed (pure simulation).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from backend.app import Settings
    from backend.area.service import AreaService
    from backend.contract.constants import GRID_AREA_ID
    from backend.roadnet.sim_siting import SitingCache, run_siting

    ap = argparse.ArgumentParser()
    ap.add_argument("--area-id", default=GRID_AREA_ID)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--level", default="rush", choices=["low", "medium", "high", "rush"])
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args()

    s = Settings()
    areas = AreaService(s.grid_path, s.sample_path, s.state_dir / "areas")
    area = areas.demo_area()[0] if args.area_id == GRID_AREA_ID else areas.get(args.area_id)
    t0 = time.perf_counter()
    res = run_siting(area, args.seed, args.level, workers=args.workers, progress=lambda m: print(" ", m, flush=True))
    path = SitingCache(s.state_dir / "siting").put(res, committed=args.area_id == GRID_AREA_ID)
    print(f"order={res.order}")
    print(f"gains={res.gains}")
    print(f"baseline_wait_s={res.baseline_wait_s} final_wait_s={res.final_wait_s} "
          f"runtime={time.perf_counter() - t0:.1f}s -> {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
