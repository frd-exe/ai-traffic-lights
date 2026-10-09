import asyncio

import httpx
import pytest

from backend.api_common import ApiError
from backend.geocode import Geocoder

OK = [{"display_name": "A", "lat": "41.0", "lon": "2.0", "boundingbox": ["40.9", "41.1", "1.9", "2.1"]},
      {"display_name": "bad", "lat": "x"},  # skipped
      {"display_name": "B", "lat": "42.0", "lon": "3.0", "boundingbox": ["42", "42", "3", "3"]}]  # degenerate bbox


class FakeTime:
    def __init__(self):
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def _geo(handler, tmp_path=None, ft=None):
    ft = ft or FakeTime()
    return Geocoder(cache_path=tmp_path / "geo.json" if tmp_path else None,
                    transport=httpx.MockTransport(handler), clock=ft.clock, sleep=ft.sleep), ft


def test_parses_and_skips_bad_items():
    g, _ = _geo(lambda r: httpx.Response(200, json=OK))
    res = asyncio.run(g.search("x")).results
    assert [r.display_name for r in res] == ["A", "B"]
    assert res[0].bbox is not None and res[1].bbox is None


def test_rate_limit_one_per_second():
    g, ft = _geo(lambda r: httpx.Response(200, json=OK))

    async def run():
        await g.search("one")
        await g.search("two")  # immediately after -> must wait ~1 s
        await g.search("one")  # cached -> no wait, no call

    asyncio.run(run())
    assert g.upstream_calls == 2
    assert ft.sleeps == [pytest.approx(1.0)]


def test_cache_persists(tmp_path):
    calls = []
    g, _ = _geo(lambda r: (calls.append(r), httpx.Response(200, json=OK))[1], tmp_path)
    asyncio.run(g.search("Barcelona"))
    g2, _ = _geo(lambda r: (calls.append(r), httpx.Response(200, json=OK))[1], tmp_path)
    asyncio.run(g2.search("barcelona"))
    assert len(calls) == 1


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(503, text="busy"),
    lambda r: httpx.Response(200, text="not json"),
    lambda r: httpx.Response(200, json={"error": "x"}),
])
def test_upstream_errors_become_502(handler):
    g, _ = _geo(handler)
    with pytest.raises(ApiError) as e:
        asyncio.run(g.search("x"))
    assert e.value.status == 502 and e.value.code == "geocode_unavailable"


def test_network_error_becomes_502():
    def boom(r):
        raise httpx.ConnectError("no route")

    g, _ = _geo(boom)
    with pytest.raises(ApiError) as e:
        asyncio.run(g.search("x"))
    assert e.value.code == "geocode_unavailable"


def test_query_validation():
    g, _ = _geo(lambda r: httpx.Response(200, json=OK))
    with pytest.raises(ApiError):
        asyncio.run(g.search("   "))
    with pytest.raises(ApiError):
        asyncio.run(g.search("x" * 300))
