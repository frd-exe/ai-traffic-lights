"""Scripts that call external services are tested with the network mocked out."""

import io
import json
import urllib.error
import urllib.parse

import pytest

from backend.tests import osm_fixture as fx
from scripts import check_keys, fetch_sample_area as fsa


def test_check_keys_never_prints_keys(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text("GEMINI_API_KEY=sekritA\nGOOGLE_MAPS_API_KEY='sekritB'\n", "utf-8")
    monkeypatch.setattr(check_keys, "ROOT", tmp_path)

    def fake_request(url, headers, body=None):
        if "generativelanguage" in url:
            return 400, json.dumps({"error": {"status": "INVALID_ARGUMENT", "message": "API key sekritA not valid"}})
        return 200, json.dumps({"routes": [{"duration": "120s", "staticDuration": "90s"}]})

    monkeypatch.setattr(check_keys, "request", fake_request)
    assert check_keys.main() == 1
    out = capsys.readouterr().out
    assert "sekrit" not in out
    assert "Gemini" in out and "FAIL" in out and "Google Routes" in out and "OK" in out


def test_check_keys_missing_env(tmp_path, monkeypatch):
    monkeypatch.setattr(check_keys, "ROOT", tmp_path)
    assert check_keys.main() == 1


# ------------------------------------------------------------------ fetch_sample_area
BBOX_ARG = "2.160,41.385,2.170,41.395"


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def ok(elements):
    return Resp(json.dumps({"version": 0.6, "elements": elements}).encode())


def http_error(code):
    return urllib.error.HTTPError("https://x", code, {504: "Gateway Timeout", 400: "Bad Request"}.get(code, "Err"),
                                  {}, io.BytesIO(b""))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated ROOT/CACHE/OUT, no real .env, no real sleeping, deterministic jitter."""
    monkeypatch.setattr(fsa, "ROOT", tmp_path)
    monkeypatch.setattr(fsa, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(fsa, "OUT", tmp_path / "sample_area.json")
    monkeypatch.delenv("OVERPASS_ENDPOINTS", raising=False)
    sleeps = []
    monkeypatch.setattr(fsa, "SLEEP", sleeps.append)
    monkeypatch.setattr(fsa.JITTER, "uniform", lambda a, b: 1.0)
    calls = []

    def install(responder):
        def urlopen(req, timeout):
            calls.append(req)
            r = responder(len(calls), req)
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(fsa, "URLOPEN", urlopen)

    return {"tmp": tmp_path, "sleeps": sleeps, "calls": calls, "install": install}


def _host(req):
    return urllib.parse.urlparse(req.full_url).netloc


def test_504_then_success(env, capsys):
    env["install"](lambda n, req: http_error(504) if n == 1 else ok([{"type": "node", "id": 1, "lat": 0, "lon": 0}]))
    assert fsa.main(["--bbox", BBOX_ARG]) == 0
    assert len(env["calls"]) == 2
    assert _host(env["calls"][0]) != _host(env["calls"][1])  # rotated to the next endpoint
    assert env["sleeps"] == [5]
    assert env["calls"][0].get_header("User-agent").startswith("ai-traffic-lights")
    out = json.loads((env["tmp"] / "sample_area.json").read_text("utf-8"))
    assert out["tiles"] == 1 and out["bbox"]["west"] == 2.16
    assert "HTTP 504" in capsys.readouterr().out


def test_all_endpoints_fail_clear_message(env, capsys):
    env["install"](lambda n, req: http_error(504))
    assert fsa.main(["--bbox", BBOX_ARG]) == 2
    out = capsys.readouterr().out
    # whole box: 4 tries, then tile 1: 4 tries, then give up
    assert len(env["calls"]) == 8
    assert env["sleeps"] == [5, 15, 45, 5, 15, 45]
    assert "splitting it into 2x2 tiles" in out
    assert "FAILED (tile 1/4)" in out and "overpass-api.de: HTTP 504" in out
    assert "What to do next" in out and "smaller box" in out and "demo city" in out
    assert not (env["tmp"] / "sample_area.json").exists()


def test_tiling_merges_and_dedupes(env):
    def responder(n, req):
        if n <= 4:
            return http_error(504)
        k = n - 4  # tiles 1..4 share node 100 and way 7
        return ok([{"type": "way", "id": 7, "nodes": [100, k]},
                   {"type": "node", "id": 100, "lat": 0, "lon": 0},
                   {"type": "node", "id": 100 + k, "lat": 1, "lon": 1, "tags": {"highway": "traffic_signals"}}])

    env["install"](responder)
    assert fsa.main(["--bbox", BBOX_ARG]) == 0
    out = json.loads((env["tmp"] / "sample_area.json").read_text("utf-8"))
    assert out["tiles"] == 4
    keys = [(el["type"], el["id"]) for el in out["osm"]["elements"]]
    assert len(keys) == len(set(keys)) == 6  # way 7, node 100, nodes 101..104
    assert env["sleeps"].count(fsa.TILE_PAUSE_S) == 3


def test_cache_reused_on_second_run(env):
    env["install"](lambda n, req: ok([{"type": "node", "id": 1, "lat": 0, "lon": 0}]))
    assert fsa.main(["--bbox", BBOX_ARG]) == 0
    assert fsa.main(["--bbox", BBOX_ARG]) == 0
    assert len(env["calls"]) == 1


def test_bad_request_is_not_retried(env, capsys):
    env["install"](lambda n, req: http_error(400))
    assert fsa.main(["--bbox", BBOX_ARG]) == 2
    assert len(env["calls"]) == 1
    assert "not retryable" in capsys.readouterr().out


def test_remark_runtime_error_counts_as_failure(env):
    bad = Resp(json.dumps({"elements": [], "remark": "runtime error: Query timed out"}).encode())
    env["install"](lambda n, req: bad if n == 1 else ok([]))
    assert fsa.main(["--bbox", BBOX_ARG]) == 0
    assert len(env["calls"]) == 2


def test_endpoints_from_env(env, monkeypatch):
    monkeypatch.setenv("OVERPASS_ENDPOINTS", "https://a.example/api/interpreter, https://b.example/api/interpreter")
    env["install"](lambda n, req: http_error(504) if n < 3 else ok([]))
    assert fsa.main(["--bbox", BBOX_ARG]) == 0
    assert [_host(c) for c in env["calls"]] == ["a.example", "b.example", "a.example"]


def test_bbox_order_errors_are_clear():
    with pytest.raises(SystemExit, match="lat,lon"):
        fsa.parse_bbox("41.395,2.17,41.385,2.16")
    with pytest.raises(SystemExit, match="too large"):
        fsa.parse_bbox("2.0,41.0,2.2,41.2")


def test_offline_check(env, capsys):
    assert fsa.offline_check(env["tmp"] / "sample_area.json") == 1
    assert "does not exist" in capsys.readouterr().out
    (env["tmp"] / "sample_area.json").write_text(json.dumps(fx.wrapped()), "utf-8")
    assert fsa.offline_check(env["tmp"] / "sample_area.json") == 0
    out = capsys.readouterr().out
    assert "1 traffic signals" in out and "7 signal candidates" in out
    (env["tmp"] / "sample_area.json").write_text("{not json", "utf-8")
    assert fsa.offline_check(env["tmp"] / "sample_area.json") == 1
    assert "not a valid sample" in capsys.readouterr().out
