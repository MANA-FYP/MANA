"""Street map tiles kept on the laptop (#161), so the dashboard's map works without internet for
places already seen with it.

A tile comes from the folder when it is there, else from OpenStreetMap, and a copy is kept. A tile
older than a week is fetched again when there is internet, as OSM's tile policy asks; without
internet the old one is served. Requests name the app in their User-Agent, as the policy asks too.
The tiles show where the person has been, so the folder stays on the laptop (it is not in git).
"""

from __future__ import annotations

import time
from pathlib import Path

UPSTREAM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
AGENT = "MANA-FYP-demo/1.0 (+https://github.com/MANA-FYP/MANA)"
FRESH_S = 7 * 24 * 3600  # OSM's tiles say they keep a week


async def fetch_osm(z: int, x: int, y: int) -> bytes | None:
    """One tile from OpenStreetMap, or None (offline, refused, not a PNG)."""
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with (
            aiohttp.ClientSession(headers={"User-Agent": AGENT}, timeout=timeout) as session,
            session.get(UPSTREAM.format(z=z, x=x, y=y)) as r,
        ):
            if r.status == 200 and r.content_type == "image/png":
                return await r.read()
    except (TimeoutError, aiohttp.ClientError, OSError):
        return None
    return None


class TileCache:
    def __init__(self, folder: Path | str, fetch=fetch_osm, clock=time.time):
        self.folder, self.fetch, self.clock = Path(folder), fetch, clock

    async def get(self, z: int, x: int, y: int) -> bytes | None:
        """The tile's PNG, or None when there is no copy and no internet. ValueError for a tile
        that does not exist at that zoom."""
        if not (0 <= z <= 19 and 0 <= x < 2**z and 0 <= y < 2**z):
            raise ValueError(f"no tile {z}/{x}/{y}")
        path = self.folder / str(z) / str(x) / f"{y}.png"
        kept = path.read_bytes() if path.exists() else None
        if kept is not None and self.clock() - path.stat().st_mtime < FRESH_S:
            return kept
        data = await self.fetch(z, x, y)
        if data is None:
            return kept  # offline: the old copy, if there is one
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data
