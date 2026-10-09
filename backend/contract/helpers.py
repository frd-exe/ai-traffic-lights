"""Deterministic id recipes and the documented demand heuristic (see docs/CONTRACT.md)."""

import hashlib

from .constants import (
    DEMAND_RATIO_HI,
    DEMAND_RATIO_LO,
    DEMAND_SCALE_MAX,
    DEMAND_SCALE_MIN,
    ID_BEARING_STEP_DEG,
    ID_COORD_DECIMALS,
    ID_HASH_HEX_LEN,
)


def _h(prefix: str, key: str) -> str:
    return prefix + hashlib.sha1(key.encode("utf-8")).hexdigest()[:ID_HASH_HEX_LEN]


def _coord(x: float) -> str:
    s = f"{round(x, ID_COORD_DECIMALS):.{ID_COORD_DECIMALS}f}"
    return "0." + "0" * ID_COORD_DECIMALS if s == "-0." + "0" * ID_COORD_DECIMALS else s


def round_bearing(bearing_deg: float) -> int:
    """Round to the nearest ID_BEARING_STEP_DEG, normalised to [0, 360)."""
    return int(round(bearing_deg / ID_BEARING_STEP_DEG) * ID_BEARING_STEP_DEG) % 360


def intersection_id(lat: float, lon: float) -> str:
    """i_ + sha1("<lat>,<lon>")[:8], coordinates rounded to 5 decimals."""
    return _h("i_", f"{_coord(lat)},{_coord(lon)}")


def approach_id(intersection_id_: str, bearing_deg: float) -> str:
    """a_ + sha1("<intersection_id>:<bearing rounded to 5 deg>")[:8]."""
    return _h("a_", f"{intersection_id_}:{round_bearing(bearing_deg)}")


def area_id(west: float, south: float, east: float, north: float, ignore_osm_signals: bool = False) -> str:
    """ar_ + sha1 of the rounded bbox (+ options), so re-analysing the same box hits the cache.
    With default options the key is just the bbox, so v0.1.0 ids are unchanged."""
    key = ",".join(_coord(v) for v in (west, south, east, north))
    if ignore_osm_signals:
        key += ";ignore_osm_signals"
    return _h("ar_", key)


def demand_scale(congestion_ratio: float) -> float:
    """Linear map: ratio <= 1.0 -> 0.4, ratio >= 2.0 -> 1.6 (multiplies the medium base flow)."""
    if congestion_ratio <= DEMAND_RATIO_LO:
        return DEMAND_SCALE_MIN
    if congestion_ratio >= DEMAND_RATIO_HI:
        return DEMAND_SCALE_MAX
    frac = (congestion_ratio - DEMAND_RATIO_LO) / (DEMAND_RATIO_HI - DEMAND_RATIO_LO)
    return round(DEMAND_SCALE_MIN + frac * (DEMAND_SCALE_MAX - DEMAND_SCALE_MIN), 6)
