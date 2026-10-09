import copy
import itertools
import random

import pytest

from backend.contract.constants import CONSOLIDATE_RADIUS_M
from backend.contract.helpers import approach_id, intersection_id
from backend.contract.models import BBox, Intersection, RoadNetwork
from backend.roadnet.geo import distance_m
from backend.roadnet.intersections import build_intersections
from backend.roadnet.overpass import (
    OverpassParseError,
    lanes_per_dir,
    oneway_dir,
    parse_overpass,
    speed_mps,
)
from backend.tests import osm_fixture as fx


def _parse(osm=None, bbox=None):
    p = parse_overpass(osm or fx.build(), BBox(**(bbox or fx.BBOX)))
    ixs = build_intersections(p.network, exclude=p.roundabout_nodes, signalised=p.signalised_nodes)
    return p, ixs


def _xy_to_latlon(x, y):
    return fx.LAT0 + y / fx.M_LAT, fx.LON0 + x / fx.M_LON


def _near(ixs, x, y, r=30):
    lat, lon = _xy_to_latlon(x, y)
    return [i for i in ixs if distance_m(lat, lon, i.lat, i.lon) < r]


def test_output_validates_against_models():
    p, ixs = _parse()
    RoadNetwork.model_validate(p.network.model_dump())
    for i in ixs:
        Intersection.model_validate(i.model_dump())


def test_drops_non_drivable_ways():
    p, _ = _parse()
    for x, y in ((250, 250), (60, 250)):  # service / footway ends
        lat, lon = _xy_to_latlon(x, y)
        assert all(distance_m(lat, lon, n.lat, n.lon) > 30 for n in p.network.nodes)
    assert {e.road_class for e in p.network.edges} == {"primary", "residential"}


def test_nodes_consolidated_no_pair_closer_than_radius():
    p, _ = _parse()
    for a, b in itertools.combinations(p.network.nodes, 2):
        assert distance_m(a.lat, a.lon, b.lat, b.lon) >= CONSOLIDATE_RADIUS_M, (a.id, b.id)


def test_dual_carriageway_crossing_is_one_junction():
    _, ixs = _parse()
    hits = _near(ixs, 0, 0, r=20)
    assert len(hits) == 1
    ix = hits[0]
    assert len(ix.approaches) == 4
    assert sorted(round(a.bearing) % 360 for a in ix.approaches) == [0, 90, 180, 270]


def test_expected_candidates_and_roundabouts_excluded():
    p, ixs = _parse()
    assert len(ixs) == 7
    assert _near(ixs, -150, -150, r=40) == []  # roundabout ring
    assert _near(ixs, 150, -150) == []  # mini roundabout
    assert len(p.roundabout_nodes) == 5


def test_osm_signal_snapped_to_junction():
    _, ixs = _parse()
    signalised = [i for i in ixs if i.has_signal_in_osm]
    assert len(signalised) == 1
    assert _near(signalised, 0, 150)


def test_boundary_entry_exit_nodes_outside_bbox():
    p, _ = _parse()
    b = BBox(**fx.BBOX)
    nodes = {n.id: n for n in p.network.nodes}
    assert len(p.network.entry_nodes) == 12 and len(p.network.exit_nodes) == 12
    for nid in p.network.entry_nodes + p.network.exit_nodes:
        n = nodes[nid]
        assert not (b.south <= n.lat <= b.north and b.west <= n.lon <= b.east)


def test_oneway_carriageways_become_two_directed_edges():
    p, ixs = _parse()
    west, centre = _near(ixs, -150, 0)[0].node_id, _near(ixs, 0, 0)[0].node_id
    fwd = [e for e in p.network.edges if (e.from_node, e.to_node) == (west, centre)]
    back = [e for e in p.network.edges if (e.from_node, e.to_node) == (centre, west)]
    assert len(fwd) == 1 and len(back) == 1
    assert fwd[0].road_class == "primary" and fwd[0].lanes == 2
    assert fwd[0].speed_limit == pytest.approx(50 / 3.6, abs=0.01)
    assert fwd[0].length_m == pytest.approx(150, abs=2)


def test_ids_follow_recipe():
    _, ixs = _parse()
    for i in ixs:
        assert i.id == intersection_id(i.lat, i.lon)
        for a in i.approaches:
            assert a.id == approach_id(i.id, a.bearing)
        assert len({a.id for a in i.approaches}) == len(i.approaches)


def _ids(ixs):
    return {i.id: sorted(a.id for a in i.approaches) for i in ixs}


def test_id_stability_reparse_and_element_order():
    p1, ix1 = _parse()
    osm = fx.build()
    shuffled = copy.deepcopy(osm)
    random.Random(4).shuffle(shuffled["elements"])
    p2, ix2 = _parse(shuffled)
    assert _ids(ix1) == _ids(ix2)
    assert p1.network == p2.network


def test_id_stability_when_bbox_shifts_slightly():
    _, ix1 = _parse()
    shifted = {k: v + (0.0002 if k in ("west", "east") else 0.0) for k, v in fx.BBOX.items()}
    _, ix2 = _parse(bbox=shifted)
    common = set(_ids(ix1)) & set(_ids(ix2))
    assert len(common) >= 6
    assert {k: _ids(ix1)[k] for k in common} == {k: _ids(ix2)[k] for k in common}


def test_parse_errors_are_clear():
    with pytest.raises(OverpassParseError, match="elements"):
        parse_overpass({"remark": "runtime error: timeout"})
    with pytest.raises(OverpassParseError, match="no drivable"):
        parse_overpass({"elements": [{"type": "way", "id": 1, "nodes": [1, 2], "tags": {"highway": "footway"}},
                                     {"type": "node", "id": 1, "lat": 0, "lon": 0},
                                     {"type": "node", "id": 2, "lat": 0, "lon": 0.001}]})


def test_tag_parsing():
    assert speed_mps({"maxspeed": "30 mph"}, "residential") == pytest.approx(13.41, abs=0.01)
    assert speed_mps({"maxspeed": "RU:urban"}, "residential") == pytest.approx(30 / 3.6, abs=0.01)
    assert speed_mps({}, "primary") == pytest.approx(50 / 3.6, abs=0.01)
    assert oneway_dir({"oneway": "-1"}, "residential") == -1
    assert oneway_dir({"junction": "roundabout"}, "residential") == 1
    assert oneway_dir({}, "motorway") == 1
    assert oneway_dir({"oneway": "no"}, "motorway") == 0
    assert lanes_per_dir({"lanes": "4"}, "primary", 1, 0) == 2
    assert lanes_per_dir({"lanes": "3", "oneway": "yes"}, "primary", 1, 1) == 3
    assert lanes_per_dir({"lanes:forward": "1"}, "primary", 1, 0) == 1
    assert lanes_per_dir({}, "residential", 1, 0) == 1
