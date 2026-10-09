"""--ai-record / --ai-replay: store supervisor plans per (seed, sim time) as JSONL and replay them
for rehearsals without calling the API (ControllerStatus.state = ai_replay)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from backend.contract.models import Plan


class PlanRecorder:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, seed: int, t: float, plans: list[Plan]) -> None:
        line = json.dumps({"seed": seed, "t": round(t, 3), "plans": [p.model_dump() for p in plans]})
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


class PlanReplay:
    """Returns, at request time t, the latest recorded batch with recorded t <= t not used yet."""

    def __init__(self, path: Path, seed: int):
        self.entries: list[tuple[float, list[Plan]]] = []
        for line in path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("seed") == seed:
                self.entries.append((float(row["t"]), [Plan.model_validate(p) for p in row["plans"]]))
        self.entries.sort(key=lambda e: e[0])
        self._next = 0

    def __len__(self) -> int:
        return len(self.entries)

    def take(self, t: float) -> list[Plan] | None:
        found = None
        while self._next < len(self.entries) and self.entries[self._next][0] <= t + 1e-9:
            found = self.entries[self._next][1]
            self._next += 1
        return found
