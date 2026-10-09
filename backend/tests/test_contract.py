import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.contract import constants as C
from backend.contract.helpers import approach_id, area_id, demand_scale, intersection_id, round_bearing
from backend.contract.models import BBox, Intersection, RoadNetwork
from scripts.export_schemas import stale_files

ROOT = Path(__file__).resolve().parents[2]


def test_schemas_not_stale():
    problems = stale_files()
    assert not problems, "run: python scripts/export_schemas.py\n" + "\n".join(problems)


def test_schema_files_use_lf():
    for p in (ROOT / "docs" / "schemas").glob("*.json"):
        assert b"\r\n" not in p.read_bytes(), p.name


def test_id_recipe_known_values():
    # Recipe: sha1("41.39000,2.16500")[:8]; documented in CONTRACT.md.
    import hashlib
    assert intersection_id(41.39, 2.165) == "i_" + hashlib.sha1(b"41.39000,2.16500").hexdigest()[:8]
    iid = intersection_id(41.39, 2.165)
    assert approach_id(iid, 92.4) == "a_" + hashlib.sha1(f"{iid}:90".encode()).hexdigest()[:8]


def test_ids_stable_under_noise():
    assert intersection_id(41.390001, 2.165002) == intersection_id(41.39, 2.165)
    iid = intersection_id(41.39, 2.165)
    assert approach_id(iid, 358.0) == approach_id(iid, 0.4)  # both round to 0
    assert round_bearing(357.6) == 0 and round_bearing(2.4) == 0 and round_bearing(2.6) == 5
    assert intersection_id(-0.000001, 0.0) == intersection_id(0.0, 0.0)  # no "-0.00000"


def test_area_id_deterministic():
    assert area_id(2.16, 41.38, 2.17, 41.40) == area_id(2.160001, 41.38, 2.17, 41.40)
    assert area_id(2.16, 41.38, 2.17, 41.40).startswith("ar_")


@pytest.mark.parametrize("ratio,scale", [(0.8, 0.4), (1.0, 0.4), (1.5, 1.0), (2.0, 1.6), (3.0, 1.6)])
def test_demand_scale(ratio, scale):
    assert demand_scale(ratio) == pytest.approx(scale)


def test_bbox_validation():
    BBox(west=2.0, south=41.0, east=2.1, north=41.1)
    with pytest.raises(ValidationError):
        BBox(west=2.1, south=41.0, east=2.0, north=41.1)


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        BBox(west=2.0, south=41.0, east=2.1, north=41.1, foo=1)


def test_constants():
    assert (C.YELLOW_S, C.ALL_RED_S, C.MIN_GREEN_S, C.MAX_RED_S, C.FIXED_PHASE_S) == (3, 2, 7, 60, 30)
    assert (C.SIM_DT_S, C.CONTROLLER_PERIOD_S, C.WS_HZ, C.GEMINI_MIN_INTERVAL_S) == (0.1, 1.0, 5, 6.0)


def test_grid_network_valid():
    raw = json.loads((ROOT / "backend" / "data" / "grid_network.json").read_text("utf-8"))
    net = RoadNetwork.model_validate(raw)
    node_ids = {n.id for n in net.nodes}
    assert len(node_ids) == 21  # 9 junctions + 12 boundary
    assert len(net.edges) == 48  # 24 two-way links
    for e in net.edges:
        assert e.from_node in node_ids and e.to_node in node_ids
    assert set(net.entry_nodes) <= node_ids and len(net.entry_nodes) == 12
    classes = {e.road_class for e in net.edges}
    assert classes == {"primary", "residential"}
    # every link is two-way
    pairs = {(e.from_node, e.to_node) for e in net.edges}
    assert all((b, a) in pairs for a, b in pairs)


def test_grid_intersections_ids_match_recipe():
    from backend.mock_server import INTERSECTIONS
    assert len(INTERSECTIONS) == 9
    for ix in INTERSECTIONS:
        Intersection.model_validate(ix.model_dump())
        assert ix.id == intersection_id(ix.lat, ix.lon)
        assert len(ix.approaches) == 4
        for a in ix.approaches:
            assert a.id == approach_id(ix.id, a.bearing)
            assert len(a.out_edges) == 3  # no U-turns
