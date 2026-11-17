"""The demo server's key: the server can drive the device, so off the USB cable it must not answer
anyone who merely reaches its address (#178).

    python -m demo.server            # prints http://localhost:8000/phone?k=<key>

The key is random, made once and kept in certs/access-key.txt (not in git), so a phone that opened
the address keeps working after the server restarts. Delete that file for a new key; every phone
and browser then needs the new address.

Every route but /static/ needs the key: as ?k= in the address, or in the cookie a page sets when
it is opened with the right ?k=. The page then reloads without ?k=, so the key is not left in the
address bar, in screenshots or in the browser's history. The websockets also refuse a page from
another site (its Origin), so a page elsewhere cannot use the cookie of this one. The scripts on
this laptop that talk to the server (demo.camera, demo.replay, tools/phone_e2e.py) read the file.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

FILE = Path(__file__).resolve().parent.parent / "certs" / "access-key.txt"
COOKIE = "mana_key"
PAGES = {"/", "/live", "/dashboard", "/phone", "/caregiver"}
REFUSED = "This demo needs its key: open the address the server printed (it ends in ?k=...)."


def read(path: Path = FILE) -> str | None:
    """The key, or None when the server has not made one yet."""
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def load_or_make(path: Path = FILE) -> str:
    key = read(path)
    if key is None:
        key = secrets.token_urlsafe(18)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner only
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key + "\n")
    return key


def with_key(url: str, key: str | None) -> str:
    return url if key is None else f"{url}{'&' if '?' in url else '?'}k={key}"


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def same_origin(request: web.Request) -> bool:
    """A browser names the page that opened the socket. Tools on this laptop send no Origin, and
    they have the key. Behind a tunnel or tailscale serve the host may come as X-Forwarded-Host."""
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc in {request.host, request.headers.get("X-Forwarded-Host")}


def middleware(key: str):
    @web.middleware
    async def check(request: web.Request, handler):
        if request.path.startswith("/static/"):  # the pages' scripts and styles, no data
            return await handler(request)
        by_address = _same(request.query.get("k", ""), key)
        if not (by_address or _same(request.cookies.get(COOKIE, ""), key)):
            raise web.HTTPForbidden(text=REFUSED)
        if request.path.startswith("/ws/") and not same_origin(request):
            raise web.HTTPForbidden(text="Only this server's own pages may connect.")
        if by_address and request.path in PAGES:
            rest = [(k, v) for k, v in request.query.items() if k != "k"]
            out = web.HTTPFound(str(request.rel_url.with_query(rest)))
            forwarded = request.headers.get("X-Forwarded-Proto") == "https"
            out.set_cookie(
                COOKIE,
                key,
                max_age=30 * 24 * 3600,
                path="/",
                httponly=True,
                samesite="Lax",
                secure=request.secure or forwarded,
            )
            raise out
        return await handler(request)

    return check
