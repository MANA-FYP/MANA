"""The demo server's key (#178): off the cable, nothing is answered without it."""

import asyncio

import pytest

pytest.importorskip("aiohttp")

from aiohttp import WSServerHandshakeError  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from demo import access  # noqa: E402
from demo.server import build_app  # noqa: E402

KEY = "test-key-123"


async def client_of(key=KEY):
    c = TestClient(TestServer(build_app(key=key)))
    await c.start_server()
    return c


def test_without_the_key_nothing_answers_not_even_the_walk():
    async def go():
        c = await client_of()
        try:
            for path in ("/phone", "/dashboard", "/api/status", "/api/walk.geojson", "/frame.jpg",
                         "/depth.jpg", "/tiles/1/1/1.png", "/ws/phone"):  # fmt: skip
                r = await c.get(path)
                assert r.status == 403, path
            r = await c.get("/api/status?k=wrong")
            assert r.status == 403
            with pytest.raises(WSServerHandshakeError):
                await c.ws_connect("/ws/phone")
            assert (await c.get("/static/phone.js")).status == 200  # scripts, no data
        finally:
            await c.close()

    asyncio.run(go())


def test_a_page_opened_with_the_key_keeps_it_in_a_cookie_and_drops_it_from_the_address():
    async def go():
        c = await client_of()
        try:
            r = await c.get(f"/phone?k={KEY}", allow_redirects=False)
            assert r.status == 302 and r.headers["Location"] == "/phone"
            cookie = r.cookies[access.COOKIE]
            assert cookie.value == KEY and cookie["httponly"] and cookie["samesite"] == "Lax"
            # the client keeps the cookie, as a browser does: the page, the API and the socket open
            assert (await c.get("/phone")).status == 200
            assert (await c.get("/api/status")).status == 200
            ws = await c.ws_connect("/ws/phone")
            await ws.close()
        finally:
            await c.close()

    asyncio.run(go())


def test_a_page_from_another_site_cannot_open_the_socket_even_with_the_cookie():
    async def go():
        c = await client_of()
        try:
            await c.get(f"/dashboard?k={KEY}")  # the cookie is set
            with pytest.raises(WSServerHandshakeError) as e:
                await c.ws_connect("/ws/phone", headers={"Origin": "https://evil.example"})
            assert e.value.status == 403
            ws = await c.ws_connect("/ws/phone", headers={"Origin": f"http://{c.host}:{c.port}"})
            await ws.close()
        finally:
            await c.close()

    asyncio.run(go())


def test_tools_on_the_laptop_connect_with_the_key_in_the_address():
    async def go():
        c = await client_of()
        try:
            ws = await c.ws_connect(access.with_key("/ws/phone", KEY))  # no Origin, no cookie
            await ws.send_json({"type": "button", "name": "stop"})
            await ws.close()
        finally:
            await c.close()

    asyncio.run(go())


def test_the_key_is_made_once_and_kept(tmp_path):
    f = tmp_path / "certs" / "access-key.txt"
    assert access.read(f) is None
    key = access.load_or_make(f)
    assert len(key) >= 20 and access.load_or_make(f) == key == access.read(f)
    assert access.with_key("http://x/phone", key) == f"http://x/phone?k={key}"
    assert access.with_key("http://x/phone", None) == "http://x/phone"


def test_with_no_key_the_app_answers_as_before():  # build_app() in the other tests
    async def go():
        c = await client_of(key=None)
        try:
            assert (await c.get("/api/status")).status == 200
        finally:
            await c.close()

    asyncio.run(go())
