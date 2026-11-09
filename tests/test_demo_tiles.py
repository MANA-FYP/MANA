"""Street map tiles kept on the laptop (#161): a stand-in for OpenStreetMap that can go offline."""

import asyncio
import importlib.util
import os

import pytest

from demo.tiles import FRESH_S, TileCache

PNG = b"\x89PNG\r\n\x1a\n" + b"tile"
pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiohttp") is None, reason="needs aiohttp")


class Upstream:
    """OpenStreetMap, or no internet."""

    def __init__(self):
        self.online, self.asked = True, []

    async def __call__(self, z, x, y):
        self.asked.append((z, x, y))
        return PNG + f"{z}/{x}/{y}".encode() if self.online else None


def run(coro):
    return asyncio.run(coro)


def test_a_tile_is_fetched_once_then_served_from_the_laptop_also_offline(tmp_path):
    osm = Upstream()
    cache = TileCache(tmp_path, osm)
    first = run(cache.get(17, 91115, 52650))
    assert first.startswith(PNG) and osm.asked == [(17, 91115, 52650)]
    osm.online = False
    assert run(cache.get(17, 91115, 52650)) == first  # from the folder
    assert osm.asked == [(17, 91115, 52650)]  # not asked again
    assert (tmp_path / "17" / "91115" / "52650.png").read_bytes() == first


def test_offline_with_no_copy_there_is_no_tile(tmp_path):
    osm = Upstream()
    osm.online = False
    assert run(TileCache(tmp_path, osm).get(17, 1, 1)) is None


@pytest.mark.parametrize("zxy", [(20, 0, 0), (2, 4, 0), (2, 0, -1), (-1, 0, 0)])
def test_a_tile_that_does_not_exist_is_refused(tmp_path, zxy):
    with pytest.raises(ValueError):
        run(TileCache(tmp_path, Upstream()).get(*zxy))


def test_a_tile_older_than_a_week_is_fetched_again_or_kept_offline(tmp_path):
    osm = Upstream()
    cache = TileCache(tmp_path, osm)
    run(cache.get(5, 1, 2))
    path = tmp_path / "5" / "1" / "2.png"
    old = path.stat().st_mtime - FRESH_S - 60
    os.utime(path, (old, old))
    osm.online = False
    assert run(cache.get(5, 1, 2)).startswith(PNG)  # offline: the old copy
    osm.online = True
    run(cache.get(5, 1, 2))
    assert len(osm.asked) == 3 and path.stat().st_mtime > old  # online: fetched and kept again


def test_the_server_serves_tiles_and_the_dashboard_asks_it(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from demo.server import WEB, build_app

    osm = Upstream()

    async def go():
        client = TestClient(TestServer(build_app(tile_cache=TileCache(tmp_path, osm))))
        await client.start_server()
        try:
            r = await client.get("/tiles/17/91115/52650.png")
            assert r.status == 200 and r.content_type == "image/png"
            assert (await r.read()).startswith(PNG)
            osm.online = False
            assert (await client.get("/tiles/17/91115/52650.png")).status == 200
            assert (await client.get("/tiles/17/1/1.png")).status == 404  # never seen: grid
            assert (await client.get("/tiles/2/9/0.png")).status == 400
        finally:
            await client.close()

        bare = TestClient(TestServer(build_app()))  # no cache given: no map, not an error
        await bare.start_server()
        try:
            assert (await bare.get("/tiles/17/1/1.png")).status == 404
        finally:
            await bare.close()

    run(go())
    assert 'const TILE = "/tiles/"' in (WEB / "dashboard.js").read_text(encoding="utf-8")
