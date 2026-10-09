from pathlib import Path

from backend.contract.models import BBox, RoadNetwork
from backend.roadnet.intersections import build_intersections
from backend.roadnet.overpass import parse_overpass
from backend.siting import prefilter
from backend.tests import osm_fixture as fx

GRID = RoadNetwork.model_validate_json(
    (Path(__file__).resolve().parents[1] / "data" / "grid_network.json").read_text("utf-8"))


def test_grid_ranks_all_nine():
    ranked, rec = prefilter.rank(GRID, build_intersections(GRID))
    assert len(ranked) == 9
    scores = [i.structural_score for i in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 1 for s in scores)
    assert 1 <= len(rec) <= prefilter.RECOMMENDED_COUNT
    assert rec == [i.id for i in ranked[:len(rec)]]


def test_grid_major_street_ranks_first():
    ranked, _ = prefilter.rank(GRID, build_intersections(GRID))
    top3_nodes = {i.node_id for i in ranked[:3]}
    assert top3_nodes == {"n21", "n22", "n23"}  # row 2 is the primary street


def test_ranking_is_deterministic():
    a = prefilter.rank(GRID, build_intersections(GRID))
    b = prefilter.rank(GRID, list(reversed(build_intersections(GRID))))
    assert a == b


def test_spacing_penalty_applies_to_close_junctions():
    ixs = build_intersections(GRID)
    feats = prefilter.features(GRID, ixs, False)
    ranked, _ = prefilter.rank(GRID, ixs)
    first = ranked[0]
    for ix in ranked[1:]:
        base = feats[ix.id].base()
        assert ix.structural_score <= round(base, 4) + 1e-9
    # n22 sits 150 m from both higher-ranked n21/n23 -> penalised below its base
    n22 = next(i for i in ranked if i.node_id == "n22")
    assert n22.structural_score < feats[n22.id].base() - 0.01
    assert first.structural_score == round(feats[first.id].base(), 4)


def test_osm_signal_bonus_and_ignore_flag():
    p = parse_overpass(fx.build(), BBox(**fx.BBOX))
    ixs = build_intersections(p.network, exclude=p.roundabout_nodes, signalised=p.signalised_nodes)
    with_sig = {i.id: i.structural_score for i in prefilter.rank(p.network, ixs, False)[0]}
    without = {i.id: i.structural_score for i in prefilter.rank(p.network, ixs, True)[0]}
    sig_id = next(i.id for i in ixs if i.has_signal_in_osm)
    assert with_sig[sig_id] > without[sig_id]


def test_features_normalised():
    feats = prefilter.features(GRID, build_intersections(GRID), False)
    for f in feats.values():
        for v in (f.degree, f.road_class, f.betweenness, f.osm_signal):
            assert 0 <= v <= 1
    assert max(f.betweenness for f in feats.values()) == 1.0
