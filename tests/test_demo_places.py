"""Places near the map: OSM's and Wikipedia's answers read into pins, kept per ~100 m cell, and the
photos through the laptop. The services are stand-ins on this machine: no test reaches the internet.
"""

import asyncio
import importlib.util

import pytest

from demo import places

WIKI_THUMB = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Mosque.jpg/320px-Mosque.jpg"
OVERPASS_REPLY = {
    "elements": [
        {"type": "node", "id": 1, "lat": 33.6561, "lon": 73.0137,
         "tags": {"amenity": "cafe", "name": "Cafe", "name:en": "Campus Cafe",
                  "wheelchair": "yes"}},
        {"type": "way", "id": 2, "center": {"lat": 33.6559, "lon": 73.0130},
         "tags": {"building": "university", "name": "FAST School of Computing"}},
        {"type": "way", "id": 3, "center": {"lat": 33.6558, "lon": 73.0131},
         "tags": {"building": "yes"}},  # no name: nothing to say on the map
        {"type": "way", "id": 4, "center": {"lat": 33.6557, "lon": 73.0139},
         "tags": {"highway": "steps", "step_count": "12", "handrail": "yes"}},
        {"type": "node", "id": 5, "lat": 33.6556, "lon": 73.0141,
         "tags": {"barrier": "kerb", "kerb": "lowered"}},
        {"type": "node", "id": 6, "lat": 33.6555, "lon": 73.0142,
         "tags": {"shop": "books", "name": "Book Shop", "wheelchair": "limited"}},
        {"type": "node", "id": 7, "lat": 33.6554, "lon": 73.0143,
         "tags": {"amenity": "toilets", "name": "Toilets", "wheelchair": "designated"}},
        {"type": "relation", "id": 8, "tags": {"name": "No centre"}},  # no place: skipped
    ]
}  # fmt: skip
WIKI_REPLY = {
    "query": {
        "pages": [
            {"title": "Faisal Mosque", "fullurl": "https://en.wikipedia.org/wiki/Faisal_Mosque",
             "coordinates": [{"lat": 33.7295, "lon": 73.0372}],
             "thumbnail": {"source": WIKI_THUMB}},
            {"title": "Elsewhere photo", "fullurl": "https://en.wikipedia.org/wiki/E",
             "coordinates": [{"lat": 33.65, "lon": 73.01}],
             "thumbnail": {"source": "https://example.com/x.jpg"}},  # not Wikimedia: no photo
            {"title": "No coordinates", "fullurl": "https://en.wikipedia.org/wiki/N"},
        ]
    }
}  # fmt: skip


def test_overpass_places_get_a_kind_and_their_wheelchair_tag():
    got = {p["page"].rsplit("/", 2)[-2] + "/" + p["page"].rsplit("/", 1)[-1]: p
           for p in places.from_overpass(OVERPASS_REPLY)}  # fmt: skip
    assert sorted(got) == ["node/1", "node/5", "node/6", "node/7", "way/2", "way/4"]
    cafe = got["node/1"]
    assert cafe["name"] == "Campus Cafe" and cafe["kind"] == "cafe" and cafe["wheelchair"] == "yes"
    assert cafe["source"] == "osm" and not cafe["small"] and cafe["photo"] is None
    school = got["way/2"]
    assert school["lat"] == 33.6559 and school["kind"] == "building"
    assert school["wheelchair"] == "unknown"
    steps = got["way/4"]
    assert steps["small"] and steps["kind"] == "steps" and steps["name"] == "Steps"
    assert steps["detail"] == "step count: 12, handrail: yes"
    assert got["node/5"]["kind"] == "kerb, lowered" and got["node/5"]["small"]
    assert got["node/6"]["kind"] == "shop (books)" and got["node/6"]["wheelchair"] == "limited"
    assert got["node/7"]["wheelchair"] == "yes"  # designated counts as yes
    assert places.from_overpass({}) == []


def test_wikipedia_pages_become_places_with_a_photo_only_from_wikimedia():
    got = places.from_wikipedia(WIKI_REPLY)
    assert [p["name"] for p in got] == ["Faisal Mosque", "Elsewhere photo"]
    assert got[0]["photo"] == WIKI_THUMB and got[0]["source"] == "wikipedia"
    assert got[0]["page"] == "https://en.wikipedia.org/wiki/Faisal_Mosque"
    assert got[1]["photo"] is None and got[0]["wheelchair"] == "unknown"
    assert places.from_wikipedia({"batchcomplete": True}) == []


def test_the_cell_is_about_100_m_and_the_query_asks_300_m_around_it():
    assert places.cell(33.65601, 73.01362) == places.cell(33.65629, 73.01371) == (33.656, 73.014)
    assert places.cell(33.6560, 73.0136) != places.cell(33.6574, 73.0136)  # 155 m north
    q = places.overpass_query(33.656, 73.014)
    assert "around:300,33.656,73.014" in q and q.endswith("out tags center 400;")
    assert "[highway=steps]" in q and "[kerb]" in q and "[wheelchair]" in q


@pytest.mark.parametrize(
    "url, ok",
    [
        (WIKI_THUMB, True),
        ("https://thumb.wikimedia.org/wikipedia/commons/thumb/e/e0/M.jpg/330px-M.jpg?utm=x", True),
        ("http://upload.wikimedia.org/a.jpg", False),  # not https
        ("https://example.com/a.jpg", False),
        ("https://upload.wikimedia.org@example.com/a.jpg", False),  # the host is example.com
        ("https://upload.wikimedia.org.example.com/a.jpg", False),
        ("https://upload.wikimedia.org/a.svg", False),  # not a picture the phone is sent
        ("", False),
    ],
)
def test_the_photo_proxy_fetches_only_wikimedia_images(url, ok):
    assert places.photo_allowed(url) is ok


needs_aiohttp = pytest.mark.skipif(
    importlib.util.find_spec("aiohttp") is None, reason="needs aiohttp"
)


@needs_aiohttp
def test_places_come_from_both_services_are_kept_and_served_offline(tmp_path, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from demo.server import build_app
    from demo.tiles import TileCache

    asked = {"busy": 0, "osm": 0, "wiki": 0}
    up = {"osm": True, "wiki": True}

    async def busy(request):  # the main instance, full
        asked["busy"] += 1
        return web.Response(status=504, text="at capacity")

    async def osm(request):
        asked["osm"] += 1
        assert "around:300" in (await request.post())["data"]
        if not up["osm"]:
            return web.Response(status=504)
        return web.json_response(OVERPASS_REPLY)

    async def wiki(request):
        asked["wiki"] += 1
        assert request.query["ggscoord"] == "33.656|73.014"
        return web.json_response(WIKI_REPLY) if up["wiki"] else web.Response(status=503)

    async def go():
        fake = web.Application()
        fake.router.add_post("/busy", busy)
        fake.router.add_post("/osm", osm)
        fake.router.add_get("/wiki", wiki)
        service = TestServer(fake)
        await service.start_server()
        base = str(service.make_url("/"))
        monkeypatch.setattr(places, "OVERPASS", [base + "busy", base + "osm"])
        monkeypatch.setattr(places, "WIKIPEDIA", base + "wiki?ggscoord={lat}|{lon}")
        client = TestClient(TestServer(build_app(tile_cache=TileCache(tmp_path))))
        await client.start_server()
        try:
            assert (await client.get("/api/places?lat=nope&lon=1")).status == 400
            assert (await client.get("/api/places?lat=91&lon=1")).status == 400
            r = await client.get("/api/places?lat=33.65601&lon=73.01362")
            assert r.status == 200
            got = await r.json()
            assert got["sources"] == {"osm": True, "wikipedia": True}
            assert len(got["places"]) == 8 and got["places"][0]["name"] == "Campus Cafe"
            assert got["places"][-1]["name"] == "Faisal Mosque"  # nearest first: 8 km away
            assert asked == {"busy": 1, "osm": 1, "wiki": 1}  # the full one, then the next
            assert (tmp_path / "places" / "33.656_73.014.json").exists()
            # 30 m away: the same cell, from memory, nothing asked
            r = await client.get("/api/places?lat=33.65629&lon=73.01371")
            assert (await r.json())["places"] == got["places"] and asked["osm"] == 1
        finally:
            await client.close()

        # a new server (the laptop restarted) and no internet: the copy on disk
        up["osm"] = up["wiki"] = False
        client = TestClient(TestServer(build_app(tile_cache=TileCache(tmp_path))))
        await client.start_server()
        try:
            r = await client.get("/api/places?lat=33.656&lon=73.014")
            assert r.status == 200 and len((await r.json())["places"]) == 8
            assert (await client.get("/api/places?lat=40&lon=10")).status == 503  # never seen
            # half an answer (OSM up, Wikipedia down) is served but not kept: asked again
            up["osm"] = True
            r = await client.get("/api/places?lat=40&lon=10")
            half = await r.json()
            assert half["sources"] == {"osm": True, "wikipedia": False}
            assert len(half["places"]) == 6
            assert not (tmp_path / "places" / "40.000_10.000.json").exists()
            before = asked["osm"]
            await client.get("/api/places?lat=40&lon=10")
            assert asked["osm"] == before + 1
        finally:
            await client.close()
            await service.close()

    asyncio.run(go())


@needs_aiohttp
def test_photos_and_place_names_come_through_the_laptop_which_keeps_them(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from demo.server import WEB, build_app
    from demo.tiles import TileCache

    fetched = []

    async def wikimedia(url):
        fetched.append(url)
        return b"\xff\xd8photo"

    async def labels(z, x, y):
        return b"\x89PNG\r\n\x1a\nlabels"

    async def go():
        app = build_app(tile_cache=TileCache(tmp_path, labels))
        app["photos"] = places.Photos(tmp_path / "photos", wikimedia)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            for _ in range(2):
                r = await client.get("/api/photo", params={"u": WIKI_THUMB})
                assert r.status == 200 and r.content_type == "image/jpeg"
                assert await r.read() == b"\xff\xd8photo"
            assert fetched == [WIKI_THUMB]  # the second from the laptop's copy
            r = await client.get("/api/photo", params={"u": "https://example.com/a.jpg"})
            assert r.status == 400
            r = await client.get("/tiles/labels/17/91115/52650.png")
            assert r.status == 200 and r.content_type == "image/png"
            assert (tmp_path / "labels" / "17" / "91115" / "52650.png").exists()
        finally:
            await client.close()

    asyncio.run(go())
    for page in ("live", "indoor"):
        html = (WEB / f"{page}.html").read_text(encoding="utf-8")
        js = (WEB / f"{page}.js").read_text(encoding="utf-8")
        assert "/static/places.js" in html and 'id="pcard"' in html and 'id="credit"' in html
        assert "/tiles/labels/" in js and "Places.want(" in js
    shared = (WEB / "places.js").read_text(encoding="utf-8")
    assert "Earthstar Geographics" in shared and "© OpenStreetMap contributors" in shared
