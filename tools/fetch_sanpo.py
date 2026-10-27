"""Fetch a working subset of SANPO-Real from Google's public bucket, with no Google tools needed.

    python3 tools/fetch_sanpo.py --root datasets/sanpo                 # 40 train, 10 test sessions
    python3 tools/fetch_sanpo.py --root datasets/sanpo --train 5 --test 2 --every 20   # quick look
    python3 tools/fetch_sanpo.py --root /tmp/sanpo-masks --masks-only --count   # what is in it

The whole dataset is about 6 TB, mostly stereo video and depth. MANA trains a 2D detector on the
left camera image, so for each chosen session this keeps only the left frames that have a
segmentation mask, and of those one frame in --every (frames next to each other are nearly the
same picture). The official train and test session lists are kept, so our test split never shares
a walk with our training split. The sessions are spread evenly over each list, so they are not
all from the same few places.

Files land as <root>/<split>/<session>/<camera>/{video_frames,segmentation_masks}/<frame>.png,
next to labelmap.json, labeltype.json and the two split lists. Files already on disk are skipped,
so the script can be stopped and started again. A request that fails is tried again three times;
a file that is still missing is skipped, and the script then exits with 1, so the run is not
taken as complete and the next run fills the gaps.

--count prints, for each "thing" class, how many objects and frames the masks on disk hold. Masks
are RGB: red is the class id; green and blue hold the instance id (green looks like the high byte).
With --masks-only the counts cost about 20 MB instead of 3 GB.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

BUCKET = "gresearch"
PREFIX = "sanpo_dataset/v0"
LIST_URL = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o"
FILE_URL = f"https://storage.googleapis.com/{BUCKET}"
CAMERAS = ("camera_chest", "camera_head")
META = ("labelmap.json", "labeltype.json")
KINDS = ("video_frames", "segmentation_masks")
SPLITS = {"train": "train_session_ids.txt", "test": "test_session_ids.txt"}
TRIES = 3
MIN_PIXELS = 100  # an object smaller than this in a mask is not counted


class Missing(Exception):
    """The file is not in the bucket, or still failed after every try."""


def file_url(name: str) -> str:
    return f"{FILE_URL}/{urllib.parse.quote(name)}"


def list_url(prefix: str, page_token: str | None = None) -> str:
    query = {"prefix": prefix, "fields": "items(name),nextPageToken", "maxResults": "1000"}
    if page_token:
        query["pageToken"] = page_token
    return f"{LIST_URL}?{urllib.parse.urlencode(query)}"


def get(url: str, tries: int = TRIES, wait: float = 1.0, sleep=time.sleep) -> bytes:
    """The body of a URL. Timeouts and server errors are tried again after 1, 2 and 4 s."""
    for attempt in range(tries + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:
                raise Missing(f"{e.code} {url}") from e
            error = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            error = e
        if attempt < tries:
            sleep(wait * 2**attempt)
    raise Missing(f"{error} after {tries + 1} tries: {url}")


def list_names(prefix: str, fetch=get) -> list[str]:
    """Every object name under a prefix, following the listing's pages."""
    names, token = [], None
    while True:
        page = json.loads(fetch(list_url(prefix, token)))
        names += [item["name"] for item in page.get("items", [])]
        token = page.get("nextPageToken")
        if not token:
            return names


def parse_ids(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def pick(ids: list[str], count: int) -> list[str]:
    """count sessions spread evenly over the official list, the same ones for every member."""
    if count < 0 or count >= len(ids):
        return list(ids)
    if count == 0:
        return []
    return ids[:: len(ids) // count][:count]


def frame_ids(mask_names: list[str], every: int) -> list[str]:
    """Frame numbers that have a mask, one in every, in order."""
    frames = sorted(Path(n).stem for n in mask_names if n.endswith(".png"))
    return frames[:: max(1, every)]


def session_files(session: str, camera: str, frames: list[str], kinds=KINDS) -> list[str]:
    """The object names to download for these frames: the left image and its mask."""
    base = f"{PREFIX}/sanpo-real/{session}/{camera}/left"
    return [f"{base}/{kind}/{f}.png" for f in frames for kind in kinds]


def local_path(root: Path, split: str, name: str) -> Path:
    """Where an object goes: <root>/<split>/<session>/<camera>/<kind>/<frame>.png."""
    session, camera, _left, kind, filename = name.split("/")[-5:]
    return root / split / session / camera / kind / filename


def download(url: str, dest: Path, fetch=get) -> bool:
    """Save one file unless it is already there. True when it was downloaded."""
    if dest.exists() and dest.stat().st_size > 0:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(fetch(url))
    tmp.replace(dest)
    return True


def count_things(root: Path) -> tuple[Counter, Counter, int]:
    """Objects and frames per "thing" class in the masks on disk, and the number of masks."""
    import numpy as np
    from PIL import Image

    names = {v: k for k, v in json.loads((root / "labelmap.json").read_text()).items()}
    kinds = json.loads((root / "labeltype.json").read_text())
    things = {i for i, n in names.items() if kinds.get(n) == "panoptic"}
    objects, frames, masks = Counter(), Counter(), 0
    for path in sorted(root.glob("*/*/*/segmentation_masks/*.png")):
        rgb = np.asarray(Image.open(path).convert("RGB")).astype(np.int64)
        ids = rgb[..., 0] * 65536 + rgb[..., 1] * 256 + rgb[..., 2]
        values, sizes = np.unique(ids, return_counts=True)
        seen = set()
        for value, size in zip(values.tolist(), sizes.tolist(), strict=True):
            cls = value // 65536
            if cls in things and size >= MIN_PIXELS:
                objects[names[cls]] += 1
                seen.add(names[cls])
        frames.update(seen)
        masks += 1
    return objects, frames, masks


def main(argv: list[str] | None = None, fetch=get) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("datasets/sanpo"))
    ap.add_argument("--train", type=int, default=40, help="train sessions, -1 for all 560")
    ap.add_argument("--test", type=int, default=10, help="test sessions, -1 for all 141")
    ap.add_argument("--every", type=int, default=10, help="keep one annotated frame in N")
    ap.add_argument("--camera", choices=(*CAMERAS, "both"), default="both")
    ap.add_argument("--masks-only", action="store_true", help="no images, for --count")
    ap.add_argument("--count", action="store_true", help="count objects per class in the masks")
    args = ap.parse_args(argv)

    args.root.mkdir(parents=True, exist_ok=True)
    cameras = CAMERAS if args.camera == "both" else (args.camera,)
    kinds = ("segmentation_masks",) if args.masks_only else KINDS
    counts = {"train": args.train, "test": args.test}
    skipped = 0

    def skip(what: str, e: Missing) -> None:
        nonlocal skipped
        skipped += 1
        print(f"  skipped {what}: {e}", file=sys.stderr, flush=True)

    for name in META:  # without these and the split lists nothing else can be done
        download(file_url(f"{PREFIX}/{name}"), args.root / name, fetch)
    for split, list_file in SPLITS.items():
        dest = args.root / "splits" / list_file
        download(file_url(f"{PREFIX}/sanpo-real/splits/{list_file}"), dest, fetch)
        sessions = pick(parse_ids(dest.read_text()), counts[split])
        for i, session in enumerate(sessions, 1):
            got = 0
            for camera in cameras:
                left = f"{PREFIX}/sanpo-real/{session}/{camera}/left"
                try:
                    masks = list_names(f"{left}/segmentation_masks/", fetch)
                except Missing as e:
                    skip(f"{session} {camera}", e)
                    continue
                for name in session_files(session, camera, frame_ids(masks, args.every), kinds):
                    try:
                        got += download(file_url(name), local_path(args.root, split, name), fetch)
                    except Missing as e:
                        skip(name.split("sanpo-real/")[-1], e)
            print(f"{split} {i}/{len(sessions)} {session}: {got} new files", flush=True)

    if args.count:
        objects, frames, n = count_things(args.root)
        print(f"\n{n} masks; objects of {MIN_PIXELS} pixels or more, and the frames showing them:")
        for cls, k in objects.most_common():
            print(f"  {cls:22s} {k:6d} objects in {frames[cls]:5d} frames")
    if skipped:
        print(f"{skipped} skipped; run again to fill the gaps", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
