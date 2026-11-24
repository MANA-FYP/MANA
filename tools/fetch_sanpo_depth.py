"""Fetch SANPO's depth maps for the frames already on disk (tools/fetch_sanpo.py), for training the
stairs segmentation with depth as a fourth channel (tools/train_stair_seg.py --depth depth_npy).

    python3 tools/fetch_sanpo_depth.py datasets/sanpo [test]

SANPO keeps each frame's depth at <session>/<camera>/left/depth_maps/<frame>.float16.gz on Google's
public bucket: gzipped float16, the first two values the height and width (720, 1280), then that
many metres. Each is saved as <camera>/depth_npy/<frame>.npy (float16, metres) for every frame of
the split (train unless given) that has a stair mask. Frames already fetched are skipped, so it
can be run again.
"""

from __future__ import annotations

import concurrent.futures as cf
import gzip
import sys
import urllib.request
from pathlib import Path

import numpy as np

URL = "https://storage.googleapis.com/gresearch/sanpo_dataset/v0/sanpo-real"


def decode(raw: bytes) -> np.ndarray:
    """A SANPO depth file's bytes as a height x width array of metres."""
    a = np.frombuffer(gzip.decompress(raw), dtype=np.float16)
    h, w = int(a[0]), int(a[1])
    return a[2 : 2 + h * w].reshape(h, w)


def jobs(root: Path, split: str = "train") -> list[tuple[str, str, str, Path]]:
    """(session, camera, frame, where to save) for every frame of the split with a mask."""
    found = []
    split = root / split
    for session in sorted(p for p in split.iterdir() if p.is_dir()) if split.is_dir() else []:
        for cam in session.iterdir():
            frames, masks = cam / "video_frames", cam / "segmentation_masks"
            if frames.is_dir() and masks.is_dir():
                for f in sorted(frames.glob("*.png")):
                    if (masks / f.name).exists():
                        found.append((session.name, cam.name, f.stem, cam / "depth_npy"))
    return found


def fetch(job) -> str | None:
    session, camera, frame, out = job
    dst = out / f"{frame}.npy"
    if dst.exists():
        return None
    url = f"{URL}/{session}/{camera}/left/depth_maps/{frame}.float16.gz"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "research-client"})
        depth = decode(urllib.request.urlopen(request, timeout=120).read())
    except Exception as e:  # a frame with no depth on the bucket, or the network
        return f"{session}/{camera}/{frame}: {str(e)[:80]}"
    out.mkdir(parents=True, exist_ok=True)
    np.save(dst, depth.astype(np.float16))
    return None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path("datasets/sanpo")
    todo = jobs(root, args[1] if len(args) > 1 else "train")
    print(f"frames needing depth: {len(todo)}", flush=True)
    with cf.ThreadPoolExecutor(max_workers=24) as ex:
        failed = [f for f in ex.map(fetch, todo) if f]
    print(f"fetched or present: {len(todo) - len(failed)} of {len(todo)}; failed {len(failed)}")
    for f in failed[:15]:
        print("  failed", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
