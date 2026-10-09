"""Small WGS84 helpers (metres / degrees). Accurate enough at city scale."""

from __future__ import annotations

import math

EARTH_R_M = 6_371_008.8


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing from point 1 to point 2, [0, 360)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(x, y)) % 360


def polyline_length_m(coords: list[list[float]]) -> float:
    """coords are [lon, lat] pairs."""
    return sum(distance_m(a[1], a[0], b[1], b[0]) for a, b in zip(coords, coords[1:]))


def bbox_side_lengths_m(west: float, south: float, east: float, north: float) -> tuple[float, float]:
    mid = (south + north) / 2
    return distance_m(mid, west, mid, east), distance_m(south, west, north, west)
