"""Street map tiles kept on the laptop (#161), so the dashboard's map works without internet for
places already seen with it.

A tile comes from the folder when it is there, else from OpenStreetMap, and a copy is kept. A tile
older than a week is fetched again when there is internet, as OSM's tile policy asks; without
internet the old one is served. Requests name the app in their User-Agent, as the policy asks too.
The tiles show where the person has been, so the folder stays on the laptop (it is not in git).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

UPSTREAM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
# the satellite layer: Esri World Imagery, free for non-commercial and educational use with its
# credit shown on the map ("Esri, Maxar, Earthstar Geographics"). Its URL is z/y/x.
SATELLITE = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
AGENT = "MANA-FYP-demo/1.0 (+https://github.com/MANA-FYP/MANA)"
FRESH_S = 7 * 24 * 3600  # OSM's tiles say they keep a week


class TileCache:
    """fetch(z, x, y) gives a tile's PNG or None; by default OpenStreetMap, over one session kept
    open, so a map's 6 to 12 tiles share a connection, as OSM's policy asks (Waqas, #162)."""

    def __init__(
        self,
        folder: Path | str,
        fetch=None,
        clock=time.time,
        upstream: str | None = None,
        content_type: str = "image/png",
    ):
        self.folder, self.clock = Path(folder), clock
        self.upstream, self.content_type = upstream or UPSTREAM, content_type
        self.ext = ".png" if content_type == "image/png" else ".jpg"
        self.given_fetch = fetch  # a test's stand-in for the internet, shared with other layers
        self.fetch = fetch or self.fetch_osm
        self.session = None

    async def fetch_osm(self, z: int, x: int, y: int) -> bytes | None:
        """One tile from OpenStreetMap, or None (offline, refused, not a PNG)."""
        import aiohttp

        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=5)
            self.session = aiohttp.ClientSession(headers={"User-Agent": AGENT}, timeout=timeout)
        try:
            async with self.session.get(self.upstream.format(z=z, x=x, y=y)) as r:
                if r.status == 200 and r.content_type == self.content_type:
                    return await r.read()
        except (TimeoutError, aiohttp.ClientError, OSError):
            return None
        return None

    def layer(self, name: str, upstream: str, content_type: str) -> TileCache:
        """Another map layer, kept in a folder of its own beside this one's tiles."""
        return TileCache(self.folder / name, self.given_fetch, self.clock, upstream, content_type)

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def get(self, z: int, x: int, y: int) -> bytes | None:
        """The tile's PNG, or None when there is no copy and no internet. ValueError for a tile
        that does not exist at that zoom."""
        if not (0 <= z <= 19 and 0 <= x < 2**z and 0 <= y < 2**z):
            raise ValueError(f"no tile {z}/{x}/{y}")
        path = self.folder / str(z) / str(x) / f"{y}{self.ext}"
        kept = path.read_bytes() if path.exists() else None
        if kept is not None and self.clock() - path.stat().st_mtime < FRESH_S:
            return kept
        data = await self.fetch(z, x, y)
        if data is None:
            return kept  # offline: the old copy, if there is one
        # all or nothing: a copy half written (the server stopped) would be served for a week,
        # and offline for ever (Waqas, #162). A copy that cannot be kept is still served.
        part = path.with_suffix(".part")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            part.write_bytes(data)
            os.replace(part, path)
        except OSError:
            part.unlink(missing_ok=True)
        return data
