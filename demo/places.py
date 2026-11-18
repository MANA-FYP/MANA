"""Places near the person, for the map: named places with what OpenStreetMap says of their
wheelchair access, the steps, kerbs and ramps around them, and Wikipedia's articles with a photo.

Asked only when the map's Places switch is on, for the ~100 m cell the map is on; nothing is
fetched ahead. The laptop keeps each cell's answer (memory and disk), so a place seen online shows
its places offline too, and the photos come through the laptop, so the phone needs no internet.
OSM's accessibility tags are sparse in Pakistan: most places say "unknown", and that is shown.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from demo.tiles import AGENT, FRESH_S

# the main public Overpass instance, then OSM France's: both are shared, and the main one was
# often full when this was written (502 or no answer in 20 s), while the French one answered
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
]
OVERPASS_S = 12  # per instance: a full one is given up on, not waited for
WIKIPEDIA = (
    "https://en.wikipedia.org/w/api.php?action=query&generator=geosearch"
    "&ggscoord={lat}|{lon}&ggsradius=1000&ggslimit=20&prop=pageimages|info|coordinates"
    "&piprop=thumbnail&pithumbsize=320&inprop=url&format=json&formatversion=2"
)
RADIUS_M = 300
# Wikimedia's image hosts: the API now gives its thumbnails from thumb.wikimedia.org
PHOTO_HOSTS = ("upload.wikimedia.org", "thumb.wikimedia.org")
PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".gif": "image/gif", ".webp": "image/webp"}  # fmt: skip
PHOTO_MAX = 2 * 1024 * 1024
WHEELCHAIR = {"yes": "yes", "designated": "yes", "limited": "limited", "no": "no"}
NAMED = ("amenity", "shop", "tourism", "leisure", "office", "healthcare", "building", "entrance")


def cell(lat: float, lon: float) -> tuple[float, float]:
    """The ~100 m cell a point is in (3 decimals: 111 m north, 93 m east at Islamabad); places
    are asked for and kept per cell."""
    return round(lat, 3), round(lon, 3)


def overpass_query(lat: float, lon: float) -> str:
    a = f"around:{RADIUS_M},{lat},{lon}"
    named = "".join(f"nwr({a})[name][{k}];" for k in NAMED)
    access = f"nwr({a})[highway=steps];nwr({a})[kerb];nwr({a})[ramp];nwr({a})[wheelchair];"
    slopes = f'way({a})[incline][highway~"^(footway|path|pedestrian)$"];'
    return f"[out:json][timeout:{OVERPASS_S}];({named}{access}{slopes});out tags center 400;"


def wheelchair(tags: dict) -> str:
    return WHEELCHAIR.get(str(tags.get("wheelchair", "")).lower(), "unknown")


def kind_of(tags: dict) -> tuple[str, bool]:
    """What a place is, in words, and whether it is a small access feature (steps, kerb, ramp,
    slope) rather than a named place."""
    if tags.get("highway") == "steps":
        return "steps", True
    if "kerb" in tags:
        return f"kerb, {tags['kerb']}".replace("_", " "), True
    if tags.get("ramp") not in (None, "no") or tags.get("ramp:wheelchair") == "yes":
        return "ramp", True
    if "incline" in tags and "name" not in tags:
        return f"slope, {tags['incline']}", True
    for k in NAMED[:-2]:
        if k in tags:
            v = tags[k].replace("_", " ")
            return (v if k == "amenity" else f"{k} ({v})" if v != "yes" else k), False
    if "entrance" in tags:
        return "entrance", False
    if "building" in tags:
        return "building", False
    return "place", False


def detail(tags: dict) -> str | None:
    """The tags that say how the place is reached, as a short line."""
    keys = ("step_count", "handrail", "incline", "kerb", "ramp", "ramp:wheelchair", "surface",
            "toilets:wheelchair", "wheelchair:description")  # fmt: skip
    got = [f"{k.replace('_', ' ')}: {tags[k]}" for k in keys if k in tags]
    return ", ".join(got) or None


def from_overpass(reply: dict) -> list[dict]:
    out = []
    for e in reply.get("elements", []):
        tags = e.get("tags") or {}
        where = e.get("center") or e
        if "lat" not in where or "lon" not in where:
            continue
        kind, small = kind_of(tags)
        if not small and "name" not in tags:
            continue  # a building or entrance with no name says nothing on the map
        out.append({
            "name": tags.get("name:en") or tags.get("name") or kind.split(",")[0].capitalize(),
            "kind": kind,
            "small": small,
            "lat": round(float(where["lat"]), 6),
            "lon": round(float(where["lon"]), 6),
            "wheelchair": wheelchair(tags),
            "detail": detail(tags),
            "source": "osm",
            "page": f"https://www.openstreetmap.org/{e.get('type', 'node')}/{e.get('id')}",
            "photo": None,
        })  # fmt: skip
    return out


def from_wikipedia(reply: dict) -> list[dict]:
    out = []
    for p in (reply.get("query") or {}).get("pages", []):
        coords = (p.get("coordinates") or [{}])[0]
        if "lat" not in coords or "lon" not in coords:
            continue
        thumb = (p.get("thumbnail") or {}).get("source")
        out.append({
            "name": p.get("title", "?"),
            "kind": "Wikipedia article",
            "small": False,
            "lat": round(float(coords["lat"]), 6),
            "lon": round(float(coords["lon"]), 6),
            "wheelchair": "unknown",
            "detail": None,
            "source": "wikipedia",
            "page": p.get("fullurl"),
            "photo": thumb if thumb and photo_allowed(thumb) else None,
        })  # fmt: skip
    return out


def photo_allowed(url: str) -> bool:
    """Only Wikimedia's own image hosts, over https, and only image files: the proxy is not a way
    to make the laptop fetch anything else."""
    u = urlsplit(url)
    suffix = os.path.splitext(u.path)[1].lower()
    return u.scheme == "https" and u.hostname in PHOTO_HOSTS and suffix in PHOTO_TYPES


def metres(lat0: float, lon0: float, lat: float, lon: float) -> float:
    n = math.radians(lat - lat0) * 6_371_000
    e = math.radians(lon - lon0) * 6_371_000 * math.cos(math.radians(lat0))
    return math.hypot(e, n)


def write_whole(path: Path, data: bytes) -> None:
    """All or nothing, as the tiles are kept (#162); a copy that cannot be kept is skipped."""
    part = path.with_suffix(".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(data)
        os.replace(part, path)
    except OSError:
        part.unlink(missing_ok=True)


class Places:
    """get(lat, lon): the places of the cell the point is in, kept in memory and in folder (or
    memory only when folder is None). Fresh for a week; offline the old copy is served."""

    def __init__(self, folder: Path | str | None, clock=time.time):
        self.folder = None if folder is None else Path(folder)
        self.clock, self.kept, self.pending = clock, {}, {}

    async def get(self, lat: float, lon: float) -> dict | None:
        key = cell(lat, lon)
        task = self.pending.get(key)
        if task is None:  # two asks for one cell share one fetch
            task = self.pending[key] = asyncio.ensure_future(self._get(key))
            task.add_done_callback(lambda _: self.pending.pop(key, None))
        return await asyncio.shield(task)

    def _path(self, key) -> Path | None:
        return None if self.folder is None else self.folder / f"{key[0]:.3f}_{key[1]:.3f}.json"

    async def _get(self, key) -> dict | None:
        kept = self.kept.get(key)
        path = self._path(key)
        if kept is None and path is not None and path.exists():
            try:
                kept = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                kept = None
        if kept is not None and self.clock() - kept["fetched"] < FRESH_S:
            self.kept[key] = kept
            return kept
        got = await fetch_both(*key)
        if not got["sources"]["osm"] and not got["sources"]["wikipedia"]:
            return kept and {**kept, "offline": True}  # offline: the old copy, if there is one
        if kept is not None and not all(got["sources"].values()):
            return {**kept, "offline": True}  # a half answer does not replace a whole one
        got["fetched"] = self.clock()
        if all(got["sources"].values()):  # only a whole answer is kept; a half one is asked again
            if len(self.kept) >= 1000:
                self.kept.clear()
            self.kept[key] = got
            if path is not None:
                write_whole(path, json.dumps(got, ensure_ascii=False).encode("utf-8"))
        return got


async def fetch_both(lat: float, lon: float) -> dict:
    """OSM's and Wikipedia's places around a point, asked at once. A source that did not answer
    is False in "sources" and adds nothing."""
    import aiohttp

    async def osm(s):
        q, wait = {"data": overpass_query(lat, lon)}, aiohttp.ClientTimeout(total=OVERPASS_S)
        for url in OVERPASS:  # busy or down: the next instance
            try:
                async with s.post(url, data=q, timeout=wait) as r:
                    if r.status == 200:
                        return from_overpass(await r.json(content_type=None))
            except (aiohttp.ClientError, TimeoutError, ValueError):
                continue
        return None

    async def wiki(s):
        try:
            async with s.get(WIKIPEDIA.format(lat=lat, lon=lon)) as r:
                if r.status == 200:
                    return from_wikipedia(await r.json(content_type=None))
        except (aiohttp.ClientError, TimeoutError, ValueError):
            pass
        return None

    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(headers={"User-Agent": AGENT}, timeout=timeout) as s:
        o, w = await asyncio.gather(osm(s), wiki(s))
    places = sorted((o or []) + (w or []), key=lambda p: metres(lat, lon, p["lat"], p["lon"]))
    return {
        "lat": lat,
        "lon": lon,
        "places": places,
        "sources": {"osm": o is not None, "wikipedia": w is not None},
    }


class Photos:
    """get(url): a Wikimedia photo's bytes and type, kept in folder, or None (offline, refused).
    fetch: a test's stand-in for the internet."""

    def __init__(self, folder: Path | str | None, fetch=None):
        self.folder = None if folder is None else Path(folder)
        self.fetch = fetch or download
        self.kept = {}  # memory only when there is no folder: the tests

    async def get(self, url: str) -> tuple[bytes, str] | None:
        if not photo_allowed(url):
            raise ValueError("not a Wikimedia image")
        ctype = PHOTO_TYPES[os.path.splitext(urlsplit(url).path)[1].lower()]
        name = hashlib.sha1(url.encode()).hexdigest()
        path = None if self.folder is None else self.folder / name
        if path is not None and path.exists():
            return path.read_bytes(), ctype
        if name in self.kept:
            return self.kept[name], ctype
        data = await self.fetch(url)
        if data is None or len(data) > PHOTO_MAX:
            return None
        if path is not None:
            write_whole(path, data)
        elif len(self.kept) < 200:
            self.kept[name] = data
        return data, ctype


async def download(url: str) -> bytes | None:
    """One photo from Wikimedia, or None (offline, refused, not an image, too big)."""
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with (
            aiohttp.ClientSession(headers={"User-Agent": AGENT}, timeout=timeout) as s,
            s.get(url) as r,
        ):
            if r.status != 200 or not r.content_type.startswith("image/"):
                return None
            if (r.content_length or 0) > PHOTO_MAX:
                return None
            return await r.read()
    except (aiohttp.ClientError, TimeoutError):
        return None
