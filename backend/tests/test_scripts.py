"""Scripts that call external services are tested with the network mocked out."""

import io
import json

from scripts import check_keys, fetch_sample_area


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


def test_fetch_sample_area_uses_user_agent_and_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_sample_area, "ROOT", tmp_path)
    monkeypatch.setattr(fetch_sample_area, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(fetch_sample_area, "OUT", tmp_path / "sample_area.json")
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        calls.append(req)
        return Resp(json.dumps({"elements": [{"type": "way", "id": 1}]}).encode())

    monkeypatch.setattr(fetch_sample_area.urllib.request, "urlopen", fake_urlopen)
    argv = ["x", "--bbox", "2.16,41.385,2.17,41.395"]
    monkeypatch.setattr("sys.argv", argv)
    fetch_sample_area.main()
    fetch_sample_area.main()  # second run must hit the cache
    assert len(calls) == 1
    assert calls[0].get_header("User-agent").startswith("ai-traffic-lights")
    out = json.loads((tmp_path / "sample_area.json").read_text("utf-8"))
    assert out["bbox"]["west"] == 2.16 and out["osm"]["elements"]
