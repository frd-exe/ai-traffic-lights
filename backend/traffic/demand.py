"""Simulated demand (no external traffic data). See CONTRACT.md §7.

Per-entry flow (veh/h) = LEVEL_FLOW_VEH_PER_H[level] * multiplier * entry_overrides.get(entry, 1).
DemandEntry.scale stores that flow relative to the medium level, so
    flow_veh_per_h(entry) = scale * LEVEL_FLOW_VEH_PER_H["medium"].

Profiles are frozen: they are never mutated. A change (POST /api/sim/demand) creates a NEW
profile whose parent_id points at the old one.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.api_common import ApiError
from backend.contract.constants import BASE_LEVEL, LEVEL_FLOW_VEH_PER_H
from backend.contract.models import (
    DataStatus,
    DemandEntry,
    DemandLevel,
    DemandProfile,
    DemandResolveRequest,
)

DEFAULT_LEVEL: DemandLevel = "medium"


def simulated_data_status() -> DataStatus:
    return DataStatus(state="baseline_only", message="Simulated demand", last_update=None,
                      calls_today=0, daily_cap=0)


def entry_flows(profile: DemandProfile) -> dict[str, float]:
    """veh/h per entry node."""
    base = LEVEL_FLOW_VEH_PER_H[BASE_LEVEL]
    return {e.entry_node_id: e.scale * base for e in profile.entries}


def total_flow(profile: DemandProfile) -> float:
    return sum(entry_flows(profile).values())


def build_profile(
    *,
    area_id: str,
    entry_nodes: list[str],
    level: DemandLevel | None = None,
    multiplier: float = 1.0,
    entry_overrides: dict[str, float] | None = None,
    departure_time: datetime | None = None,
    parent_id: str | None = None,
    profile_id: str | None = None,
) -> DemandProfile:
    """Pure function: same inputs -> same entries (only id/created_at differ)."""
    level = level or DEFAULT_LEVEL
    overrides = dict(entry_overrides or {})
    unknown = sorted(set(overrides) - set(entry_nodes))
    if unknown:
        raise ApiError(400, "unknown_entry_node", "entry_overrides contains ids that are not entry nodes of this area",
                       {"ids": unknown, "entry_nodes": sorted(entry_nodes)})
    level_scale = LEVEL_FLOW_VEH_PER_H[level] / LEVEL_FLOW_VEH_PER_H[BASE_LEVEL]
    entries = [
        DemandEntry(entry_node_id=n, congestion_ratio=None,
                    scale=round(level_scale * multiplier * overrides.get(n, 1.0), 6))
        for n in sorted(entry_nodes)
    ]
    return DemandProfile(
        id=profile_id or "dp_" + uuid.uuid4().hex[:10],
        area_id=area_id,
        source="baseline_only",
        level=level,
        created_at=datetime.now(timezone.utc).replace(microsecond=0),
        departure_time=departure_time,
        entries=entries,
        multiplier=multiplier,
        entry_overrides=overrides,
        parent_id=parent_id,
    )


def derive_profile(
    parent: DemandProfile,
    entry_nodes: list[str],
    *,
    level: DemandLevel | None = None,
    multiplier: float | None = None,
    entry_overrides: dict[str, float] | None = None,
) -> DemandProfile:
    """New frozen profile = parent with the given fields replaced (None keeps the parent value)."""
    return build_profile(
        area_id=parent.area_id or "",
        entry_nodes=entry_nodes,
        level=level or parent.level,
        multiplier=parent.multiplier if multiplier is None else multiplier,
        entry_overrides=parent.entry_overrides if entry_overrides is None else entry_overrides,
        departure_time=parent.departure_time,
        parent_id=parent.id,
    )


class DemandStore:
    """Frozen profiles by id; persisted as JSON so sessions survive a restart."""

    def __init__(self, directory: Path | None = None):
        self._dir = directory
        self._mem: dict[str, DemandProfile] = {}
        self._lock = threading.Lock()

    def put(self, profile: DemandProfile) -> DemandProfile:
        with self._lock:
            if profile.id in self._mem:
                raise ValueError(f"profile {profile.id} already exists (profiles are frozen)")
            self._mem[profile.id] = profile
            if self._dir:
                self._dir.mkdir(parents=True, exist_ok=True)
                (self._dir / f"{profile.id}.json").write_bytes(profile.model_dump_json(indent=1).encode("utf-8"))
        return profile.model_copy(deep=True)

    def get(self, profile_id: str) -> DemandProfile:
        with self._lock:
            p = self._mem.get(profile_id)
            if p is None and self._dir and (self._dir / f"{profile_id}.json").exists():
                p = DemandProfile.model_validate_json((self._dir / f"{profile_id}.json").read_text("utf-8"))
                self._mem[profile_id] = p
        if p is None:
            raise ApiError(404, "unknown_demand_profile", f"demand profile {profile_id} not found")
        return p.model_copy(deep=True)  # callers can't mutate the frozen original


def resolve(req: DemandResolveRequest, entry_nodes: list[str], store: DemandStore) -> DemandProfile:
    """POST /api/demand/resolve. Always simulated demand; google_* sources are rejected."""
    if req.source != "baseline":
        raise ApiError(400, "source_not_supported",
                       f"source '{req.source}' is not supported: this build uses simulated demand only. "
                       "Send source='baseline' (or omit it) with a level and optional multiplier.",
                       {"supported": ["baseline"]})
    profile = build_profile(area_id=req.area_id, entry_nodes=entry_nodes, level=req.level,
                            multiplier=req.multiplier, entry_overrides=req.entry_overrides,
                            departure_time=req.departure_time)
    return store.put(profile)
