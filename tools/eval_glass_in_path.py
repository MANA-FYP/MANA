"""Glass ahead, decided per photo exactly as the phone demo decides it (GlassSegmenter.in_path,
then the obstacle confidence of demo/server.py): how often it fires on GSD-S test photos with glass
in the middle third of the picture, and how often on ADE20K validation photos with nothing glassy
labelled, the false alarms a user would hear.

    python3 tools/eval_glass_in_path.py --gsds datasets/gsds --ade datasets/ADEChallengeData2016 \\
        runs/glass_seg.pt [more weights]

ADE20K photos count as without glass when no pixel is labelled window, door, mirror, display case,
screen door, screen, monitor, television, vehicle (car, bus, truck), shower or drinking glass
(objectInfo150 indices below). Indoor: the photo's scene name (sceneCategories.txt) is a room,
office, corridor, hall, shop and the like.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo import vision  # noqa: E402

CONFIDENCE = 0.4  # demo/server.py MIN_OBSTACLE_CONFIDENCE: weaker glass is not an obstacle
GLASSY = {9, 15, 21, 28, 56, 59, 81, 84, 90, 131, 142, 144, 146, 148}
INDOOR = (
    "room", "office", "corridor", "kitchen", "bathroom", "bedroom", "lobby", "hall", "terminal",
    "gallery", "store", "shop", "library", "classroom", "attic", "basement", "restaurant", "bar",
    "museum", "gym", "station", "staircase", "closet", "nursery",
)  # fmt: skip


def glass_ahead(seg: vision.GlassSegmenter, photo: Path) -> bool:
    """Would the demo report glass in the path for this photo?"""
    w, h = Image.open(photo).size
    seg.segment(photo.read_bytes())
    box = seg.in_path(w, h)
    return box is not None and box.confidence >= CONFIDENCE


def shows_glass_ahead(mask_png: Path, size: int = 320, share: float = 0.02) -> bool:
    """Glass labelled over at least `share` of the middle third, as in_path asks of the model."""
    m = np.asarray(Image.open(mask_png).convert("L").resize((size, size), Image.NEAREST)) > 127
    return m[:, size // 3 : 2 * (size // 3)].mean() >= share


def without_glass(ade: Path) -> tuple[list[Path], list[Path]]:
    """ADE20K validation photos with nothing glassy labelled, and those of them indoors."""
    lines = (ade / "sceneCategories.txt").read_text().splitlines()
    scenes = dict(line.split()[:2] for line in lines if line.strip())
    clear, indoor = [], []
    for ann in sorted((ade / "annotations/validation").glob("*.png")):
        if GLASSY & set(np.unique(np.asarray(Image.open(ann))).tolist()):
            continue
        photo = ade / "images/validation" / f"{ann.stem}.jpg"
        clear.append(photo)
        if any(w in scenes.get(ann.stem, "") for w in INDOOR):
            indoor.append(photo)
    return clear, indoor


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("weights", nargs="+", type=Path)
    ap.add_argument("--gsds", type=Path, default=Path("datasets/gsds"))
    ap.add_argument("--ade", type=Path, default=Path("datasets/ADEChallengeData2016"))
    a = ap.parse_args(argv)
    test = a.gsds / "test"
    ahead = [
        p
        for p in sorted((test / "images").glob("*.jpg"))
        if shows_glass_ahead(test / "masks" / f"{p.stem}.png")
    ]
    clear, indoor = without_glass(a.ade)
    print(f"GSD-S test photos with glass ahead: {len(ahead)}; ADE20K validation photos without "
          f"glass: {len(clear)}, indoor {len(indoor)}", flush=True)  # fmt: skip
    for w in a.weights:
        seg = vision.GlassSegmenter.load(str(w))
        if seg is None:
            return 1
        found = sum(glass_ahead(seg, p) for p in ahead)
        false = {p for p in clear if glass_ahead(seg, p)}
        inside = sum(p in false for p in indoor)
        print(
            f"{w.name}: glass ahead found {found}/{len(ahead)} ({found / max(1, len(ahead)):.0%}); "
            f"false glass ahead {len(false)}/{len(clear)} ({len(false) / max(1, len(clear)):.0%}), "
            f"indoor {inside}/{len(indoor)} ({inside / max(1, len(indoor)):.0%})",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
