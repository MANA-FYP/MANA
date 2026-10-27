"""Fetch a working subset of SANPO-Real from Google's public bucket, with no Google tools needed.

    python3 tools/fetch_sanpo.py --root datasets/sanpo                 # 40 train, 10 test sessions
    python3 tools/fetch_sanpo.py --root datasets/sanpo --train 5 --test 2 --every 20   # quick look

The whole dataset is about 6 TB, mostly stereo video and depth. MANA trains a 2D detector on the
left camera image, so for each chosen session this keeps only the left frames that have a
segmentation mask, and of those one frame in --every (frames next to each other are nearly the
same picture). The official train and test session lists are kept, so our test split never shares
a walk with our training split.

Files land as <root>/<split>/<session>/<camera>/{video_frames,segmentation_masks}/<frame>.png,
next to labelmap.json, labeltype.json and the two split lists. Files already on disk are skipped,
so the script can be stopped and started again.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BUCKET = "gresearch"
PREFIX = "sanpo_dataset/v0"
LIST_URL = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o"
FILE_URL = f"https://storage.googleapis.com/{BUCKET}"
CAMERAS = ("camera_chest", "camera_head")
META = ("labelmap.json", "labeltype.json")
KINDS = ("video_frames", "segmentation_masks")
SPLITS = {"train": "train_session_ids.txt", "test": "test_session_ids.txt"}


def file_url(name: str) -> str:
    return f"{FILE_URL}/{urllib.parse.quote(name)}"


def list_url(prefix: str, page_token: str | None = None) -> str:
    query = {"prefix": prefix, "fields": "items(name),nextPageToken", "maxResults": "1000"}
    if page_token:
        query["pageToken"] = page_token
    return f"{LIST_URL}?{urllib.parse.urlencode(query)}"


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


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
    """The first count sessions in the official list, so every member gets the same ones."""
    return ids if count < 0 else ids[:count]


def frame_ids(mask_names: list[str], every: int) -> list[str]:
    """Frame numbers that have a mask, one in every, in order."""
    frames = sorted(Path(n).stem for n in mask_names if n.endswith(".png"))
    return frames[:: max(1, every)]


def session_files(session: str, camera: str, frames: list[str]) -> list[str]:
    """The object names to download for these frames: the left image and its mask."""
    base = f"{PREFIX}/sanpo-real/{session}/{camera}/left"
    return [f"{base}/{kind}/{f}.png" for f in frames for kind in KINDS]


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("datasets/sanpo"))
    ap.add_argument("--train", type=int, default=40, help="train sessions, -1 for all 560")
    ap.add_argument("--test", type=int, default=10, help="test sessions, -1 for all 141")
    ap.add_argument("--every", type=int, default=10, help="keep one annotated frame in N")
    ap.add_argument("--camera", choices=(*CAMERAS, "both"), default="both")
    args = ap.parse_args(argv)

    args.root.mkdir(parents=True, exist_ok=True)
    for name in META:
        download(file_url(f"{PREFIX}/{name}"), args.root / name)
    cameras = CAMERAS if args.camera == "both" else (args.camera,)
    counts = {"train": args.train, "test": args.test}
    for split, list_file in SPLITS.items():
        dest = args.root / "splits" / list_file
        download(file_url(f"{PREFIX}/sanpo-real/splits/{list_file}"), dest)
        sessions = pick(parse_ids(dest.read_text()), counts[split])
        for i, session in enumerate(sessions, 1):
            got = 0
            for camera in cameras:
                left = f"{PREFIX}/sanpo-real/{session}/{camera}/left"
                masks = list_names(f"{left}/segmentation_masks/")
                for name in session_files(session, camera, frame_ids(masks, args.every)):
                    got += download(file_url(name), local_path(args.root, split, name))
            print(f"{split} {i}/{len(sessions)} {session}: {got} new files", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
