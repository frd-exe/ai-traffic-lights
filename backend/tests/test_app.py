"""Real backend (backend/app.py) with isolated state dirs; no network."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import Settings, create_app
from backend.contract.constants import GRID_AREA_ID
from backend.contract.models import AreaResponse, DemandProfile, DemandResolveResponse, ErrorResponse
from backend.tests import osm_fixture as fx


def _client(tmp_path, sample=False, **kw) -> TestClient:
    sample_path = tmp_path / "sample_area.json"
    if sample:
        sample_path.write_text(json.dumps(fx.wrapped()), "utf-8")
    return TestClient(create_app(Settings(state_dir=tmp_path / "state", sample_path=sample_path, **kw)))


def _area(c, bbox=None, **kw):
    r = c.post("/api/area", json={"bbox": bbox or fx.BBOX, **kw})
    assert r.status_code == 200, r.text
    return AreaResponse.model_validate(r.json()), r.headers["X-Area-Cache"]


# ------------------------------------------------------------------ synthetic grid
def test_no_sample_serves_grid(tmp_path):
    c = _client(tmp_path)
    area, hit = _area(c)
    assert area.source == "synthetic_grid" and area.area_id == GRID_AREA_ID
    assert len(area.intersections) == 9 and hit == "computed"
    assert _area(c)[1] == "memory"
    demo = AreaResponse.model_validate(c.get("/api/demo-area").json())
    assert demo == area
    assert AreaResponse.model_validate(c.get(f"/api/area/{GRID_AREA_ID}").json()) == area


def test_demo_city_flag_even_with_sample(tmp_path):
    c = _client(tmp_path, sample=True)
    area, _ = _area(c, demo_city=True)
    assert area.source == "synthetic_grid"


def test_grid_persisted_across_restart(tmp_path):
    first, _ = _area(_client(tmp_path))
    second, hit = _area(_client(tmp_path))
    assert hit == "disk" and second == first


# ------------------------------------------------------------------ OSM sample
def test_osm_area_from_sample(tmp_path):
    c = _client(tmp_path, sample=True)
    area, hit = _area(c)
    assert area.source == "osm" and area.area_id.startswith("ar_") and hit == "computed"
    assert len(area.intersections) == 7
    assert len(area.network.entry_nodes) == 12


def test_osm_area_cache_and_id_stability(tmp_path):
    c = _client(tmp_path, sample=True)
    a1, _ = _area(c)
    a2, hit2 = _area(c)
    assert hit2 == "memory" and a1 == a2
    a3, hit3 = _area(_client(tmp_path, sample=True))  # restart
    assert hit3 == "disk" and a3 == a1
    # a cold recompute (cache wiped) yields identical ids
    for p in (tmp_path / "state" / "areas").glob("*.json"):
        p.unlink()
    a4, hit4 = _area(_client(tmp_path, sample=True))
    assert hit4 == "computed"
    assert [(i.id, [a.id for a in i.approaches]) for i in a4.intersections] == \
           [(i.id, [a.id for a in i.approaches]) for i in a1.intersections]


def test_ignore_osm_signals_is_a_different_area(tmp_path):
    c = _client(tmp_path, sample=True)
    a, _ = _area(c)
    b, _ = _area(c, ignore_osm_signals=True)
    assert a.area_id != b.area_id


def test_bbox_too_large(tmp_path):
    c = _client(tmp_path, sample=True)
    r = c.post("/api/area", json={"bbox": {"west": 2.0, "south": 41.3, "east": 2.1, "north": 41.4}})
    assert r.status_code == 400 and ErrorResponse.model_validate(r.json()).error.code == "bbox_too_large"


def test_bbox_outside_sample(tmp_path):
    c = _client(tmp_path, sample=True)
    r = c.post("/api/area", json={"bbox": {"west": 13.40, "south": 52.50, "east": 13.41, "north": 52.51}})
    assert r.status_code == 404
    err = ErrorResponse.model_validate(r.json()).error
    assert err.code == "area_not_available" and "demo city" in err.message


def test_rate_limit_counts_only_computations(tmp_path):
    c = _client(tmp_path, sample=True, area_rate_limit_per_min=1)
    _area(c)
    _area(c)  # cache hit: not limited
    shifted = {k: v + (0.0001 if k in ("west", "east") else 0.0) for k, v in fx.BBOX.items()}
    r = c.post("/api/area", json={"bbox": shifted})
    assert r.status_code == 429 and ErrorResponse.model_validate(r.json()).error.code == "rate_limited"


# ------------------------------------------------------------------ demand
def _resolve(c, **body):
    return c.post("/api/demand/resolve", json={"area_id": GRID_AREA_ID, **body})


def test_demand_resolve_levels_and_frozen(tmp_path):
    c = _client(tmp_path)
    _area(c)
    rush = DemandResolveResponse.model_validate(_resolve(c, level="rush").json()).demand_profile
    low = DemandResolveResponse.model_validate(_resolve(c, level="low").json()).demand_profile
    assert sum(e.scale for e in rush.entries) > sum(e.scale for e in low.entries)
    again = DemandResolveResponse.model_validate(_resolve(c, level="rush").json()).demand_profile
    assert again.id != rush.id
    assert DemandProfile.model_validate(c.get(f"/api/demand/{rush.id}").json()) == rush


def test_demand_multiplier_overrides_and_errors(tmp_path):
    c = _client(tmp_path)
    area, _ = _area(c)
    entry = area.network.entry_nodes[0]
    r = _resolve(c, level="medium", multiplier=2.0, entry_overrides={entry: 1.5})
    p = DemandResolveResponse.model_validate(r.json())
    assert p.data_status.message == "Simulated demand"
    scales = {e.entry_node_id: e.scale for e in p.demand_profile.entries}
    assert scales[entry] == pytest.approx(3.0)
    assert all(v == pytest.approx(2.0) for k, v in scales.items() if k != entry)
    for body, status, code in (
        ({"source": "google_live"}, 400, "source_not_supported"),
        ({"entry_overrides": {"n_nope": 2}}, 400, "unknown_entry_node"),
        ({"multiplier": 5}, 422, "validation_error"),
    ):
        r = _resolve(c, **body)
        assert r.status_code == status and ErrorResponse.model_validate(r.json()).error.code == code
    r = c.post("/api/demand/resolve", json={"area_id": "ar_unknown"})
    assert r.status_code == 404


# ------------------------------------------------------------------ geocode via the app
def test_geocode_endpoint_with_mocked_nominatim(tmp_path):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json=[{"display_name": "Eixample, Barcelona", "lat": "41.39", "lon": "2.165",
                                          "boundingbox": ["41.38", "41.40", "2.15", "2.18"]}])

    c = _client(tmp_path, geocode_transport=httpx.MockTransport(handler))
    r = c.get("/api/geocode", params={"q": "Eixample"})
    assert r.status_code == 200
    assert r.json()["results"][0]["bbox"] == {"west": 2.15, "south": 41.38, "east": 2.18, "north": 41.40}
    c.get("/api/geocode", params={"q": "  eixample "})  # cached (normalised key)
    assert len(calls) == 1
    assert calls[0].headers["user-agent"].startswith("ai-traffic-lights")
