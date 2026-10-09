import pytest
from pydantic import ValidationError

from backend.api_common import ApiError
from backend.contract.constants import LEVEL_FLOW_VEH_PER_H
from backend.contract.models import DemandResolveRequest
from backend.traffic.demand import (
    DemandStore,
    build_profile,
    derive_profile,
    entry_flows,
    resolve,
    simulated_data_status,
    total_flow,
)

ENTRIES = ["n01", "n02", "n10", "n14"]


def _p(**kw):
    return build_profile(area_id="a", entry_nodes=ENTRIES, **kw)


def test_levels_documented_flows():
    for level, flow in LEVEL_FLOW_VEH_PER_H.items():
        p = _p(level=level)
        assert p.source == "baseline_only" and p.level == level
        assert all(f == pytest.approx(flow) for f in entry_flows(p).values())
    assert total_flow(_p(level="low")) < total_flow(_p(level="medium")) < total_flow(_p(level="high")) \
        < total_flow(_p(level="rush"))


def test_default_level_is_medium():
    assert _p().level == "medium"


def test_multiplier_scales_every_entry():
    base, doubled = _p(level="high"), _p(level="high", multiplier=2.0)
    for e in ENTRIES:
        assert entry_flows(doubled)[e] == pytest.approx(2 * entry_flows(base)[e])
    assert doubled.multiplier == 2.0


def test_entry_overrides_apply_per_entry():
    p = _p(level="medium", multiplier=1.5, entry_overrides={"n10": 2.0})
    f = entry_flows(p)
    medium = LEVEL_FLOW_VEH_PER_H["medium"]
    assert f["n10"] == pytest.approx(medium * 1.5 * 2.0)
    assert f["n01"] == pytest.approx(medium * 1.5)
    assert p.entry_overrides == {"n10": 2.0}


def test_unknown_override_entry_rejected():
    with pytest.raises(ApiError) as e:
        _p(entry_overrides={"nope": 2.0})
    assert e.value.code == "unknown_entry_node"


def test_multiplier_range_enforced_by_contract():
    for bad in (0.1, 3.5):
        with pytest.raises(ValidationError):
            DemandResolveRequest(area_id="a", multiplier=bad)
    with pytest.raises(ValidationError):
        DemandResolveRequest(area_id="a", entry_overrides={"n01": 0})


def test_deterministic_entries():
    a, b = _p(level="rush", multiplier=1.3), _p(level="rush", multiplier=1.3)
    assert a.entries == b.entries and a.id != b.id


def test_store_freezes_profiles(tmp_path):
    store = DemandStore(tmp_path)
    p = store.put(_p(level="high"))
    got = store.get(p.id)
    got.entries[0].scale = 99.0  # mutate the copy
    assert store.get(p.id).entries[0].scale != 99.0
    with pytest.raises(ValueError):
        store.put(p)
    # persisted: a new store (restart) finds it
    assert DemandStore(tmp_path).get(p.id) == p
    with pytest.raises(ApiError) as e:
        store.get("dp_missing")
    assert e.value.code == "unknown_demand_profile"


def test_resolve_gives_new_id_each_time_and_rejects_google():
    store = DemandStore()
    r1 = resolve(DemandResolveRequest(area_id="a", level="rush"), ENTRIES, store)
    r2 = resolve(DemandResolveRequest(area_id="a", level="rush"), ENTRIES, store)
    assert r1.id != r2.id and r1.entries == r2.entries
    assert store.get(r1.id) == r1
    for src in ("google_live", "google_snapshot"):
        with pytest.raises(ApiError) as e:
            resolve(DemandResolveRequest(area_id="a", source=src), ENTRIES, store)
        assert e.value.code == "source_not_supported" and "simulated demand" in e.value.message


def test_derive_keeps_unset_fields_and_links_parent():
    parent = _p(level="high", multiplier=1.5, entry_overrides={"n01": 2.0})
    child = derive_profile(parent, ENTRIES, multiplier=0.5)
    assert child.parent_id == parent.id and child.id != parent.id
    assert child.level == "high" and child.entry_overrides == {"n01": 2.0} and child.multiplier == 0.5


def test_data_status_is_simulated():
    ds = simulated_data_status()
    assert ds.state == "baseline_only" and ds.message == "Simulated demand"
