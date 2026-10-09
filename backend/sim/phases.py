"""Geometry phases; opposing arrivals share a phase, turns use reservations.

Bearings describe the upstream direction. Pair approaches within 20 degrees of
opposition, preferring the most nearly opposite pair. Unpaired arms get their
own phase (T/five-arm junctions). Crossing movements are serialized by the
engine: green permits requesting a reservation, not unprotected left turns.
"""
from backend.contract.models import Intersection, Phase


def derive_phases(intersection: Intersection) -> list[Phase]:
    remaining = sorted(intersection.approaches, key=lambda a: (a.bearing % 180, a.bearing, a.id))
    groups = []
    while remaining:
        a = remaining.pop(0)
        candidates = [(abs(((b.bearing - a.bearing) % 360) - 180), b.id, b)
                      for b in remaining]
        opposite = min(candidates, default=None, key=lambda x: x[:2])
        group = [a.id]
        if opposite and opposite[0] <= 20:
            group.append(opposite[2].id)
            remaining.remove(opposite[2])
        groups.append(group)
    return [Phase(id=f"{intersection.id}:p{k}", approach_ids=g) for k, g in enumerate(groups)]
