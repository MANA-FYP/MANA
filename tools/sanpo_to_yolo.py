"""Turn the SANPO frames on disk into a YOLO detection dataset, for fine-tuning in #49.

    python3 tools/sanpo_to_yolo.py --root datasets/sanpo --out datasets/sanpo_yolo
    yolo detect train data=datasets/sanpo_yolo/data.yaml model=yolo26s.pt imgsz=640 epochs=50

SANPO has segmentation masks, not boxes. Each object of a "thing" class (labeltype.json marks
them "panoptic": stairs, pedestrian, vehicle, pole, opening-door, obstacle ...) becomes one box
around its pixels; "stuff" classes such as road and sidewalk are left out. Masks are RGB: red is
the class id, green and blue the instance id (as tools/fetch_sanpo.py --count reads them). An
object smaller than --min-pixels (100) is left out, as in the counts in docs/datasets.md.

Parts: SANPO's own test sessions are the test part. Of its training sessions, --val (10 %) are
held out **by session** for validation, with a fixed seed, so neighbouring frames of one walk are
never in two parts. Each run writes the parts from scratch, so a run with another seed or more
sessions leaves nothing behind in the old parts. It needs at least two training sessions, or the
validation part would be empty. A frame with no object is kept with an empty label file (a
background image).

Written to --out: images/<part>/ (hard links to the frames where the disk allows, else copies),
labels/<part>/ (one line per box: class cx cy w h, all 0 to 1), data.yaml (the paths and the
class names, which every model in #49 trains and is tested on) and parts.json (the sessions of
each part and the objects of each class in it).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
from collections import Counter
from pathlib import Path

MIN_PIXELS = 100
PARTS = ("train", "val", "test")


def thing_classes(root: Path) -> dict[int, str]:
    """SANPO's class id -> name of every "thing" class, in id order."""
    names = json.loads((root / "labelmap.json").read_text())
    kinds = json.loads((root / "labeltype.json").read_text())
    return {
        i: n for n, i in sorted(names.items(), key=lambda kv: kv[1]) if kinds.get(n) == "panoptic"
    }


def boxes(mask, things: dict[int, int], min_pixels: int = MIN_PIXELS):
    """(YOLO class, cx, cy, w, h) of every object of a thing class in an RGB mask, 0 to 1, in the
    order of the mask's ids. things maps SANPO's class id to the YOLO class index."""
    import numpy as np

    rgb = np.asarray(mask).astype(np.int64)
    rows, cols = rgb.shape[:2]
    ids = rgb[..., 0] * 65536 + rgb[..., 1] * 256 + rgb[..., 2]
    found = []
    values, sizes = np.unique(ids, return_counts=True)
    for value, size in zip(values.tolist(), sizes.tolist(), strict=True):
        cls = value // 65536
        if cls not in things or size < min_pixels:
            continue
        ys, xs = np.nonzero(ids == value)
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        found.append((
            things[cls],
            (x0 + x1) / 2 / cols, (y0 + y1) / 2 / rows, (x1 - x0) / cols, (y1 - y0) / rows,
        ))  # fmt: skip
    return found


def split_sessions(train: list[str], test: list[str], val_share: float = 0.1, seed: int = 0):
    """The sessions of each part: val_share of the training sessions held out for validation."""
    pool = sorted(train)
    random.Random(seed).shuffle(pool)
    n_val = round(len(pool) * val_share) if len(pool) > 1 else 0
    n_val = max(1, n_val) if len(pool) > 1 and val_share > 0 else n_val
    return {"train": sorted(pool[n_val:]), "val": sorted(pool[:n_val]), "test": sorted(test)}


def place(src: Path, dest: Path) -> None:
    """A hard link to src at dest, or a copy where the disk cannot link."""
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)


def frames_of(root: Path, split: str):
    """(session, camera, image, mask) of every frame of a SANPO split on disk with both files."""
    for mask in sorted((root / split).glob("*/*/segmentation_masks/*.png")):
        image = mask.parent.parent / "video_frames" / mask.name
        if image.exists():
            yield mask.parts[-4], mask.parts[-3], image, mask


def convert(root: Path, out: Path, val_share: float = 0.1, seed: int = 0,
            min_pixels: int = MIN_PIXELS) -> dict:  # fmt: skip
    from PIL import Image

    sanpo = thing_classes(root)
    names = list(sanpo.values())
    things = {cls: k for k, cls in enumerate(sanpo)}
    sessions = {s: sorted({f[0] for f in frames_of(root, s)}) for s in ("train", "test")}
    parts = split_sessions(sessions["train"], sessions["test"], val_share, seed)
    part_of = {(s, sess): p for p, ss in parts.items() for sess in ss for s in ("train", "test")
               if sess in sessions[s]}  # fmt: skip
    objects = {p: Counter() for p in PARTS}
    images = Counter()
    if not parts["val"]:
        raise ValueError(
            f"no validation sessions: {len(parts['train'])} training session(s) with frames and "
            f"--val {val_share}; yolo detect train stops on an empty images/val. Fetch more "
            "training sessions (fetch_sanpo.py --train) or raise --val."
        )
    # each part from scratch: frames an earlier run with another split left in another part would
    # be in two parts, and YOLO reads the folders, not parts.json
    for p in PARTS:
        for kind in ("images", "labels"):
            shutil.rmtree(out / kind / p, ignore_errors=True)
            (out / kind / p).mkdir(parents=True)
    for split in ("train", "test"):
        for session, camera, image, mask in frames_of(root, split):
            p = part_of[(split, session)]
            stem = f"{session}__{camera}__{image.stem}"
            with Image.open(mask) as m:
                found = boxes(m.convert("RGB"), things, min_pixels)
            place(image, out / "images" / p / f"{stem}{image.suffix}")
            lines = [f"{k} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for k, cx, cy, w, h in found]
            (out / "labels" / p / f"{stem}.txt").write_text("".join(f"{line}\n" for line in lines))
            objects[p].update(names[k] for k, *_ in found)
            images[p] += 1
    # JSON strings are valid YAML, quoted: a '#' in the path or a ':' in a name stays text
    yaml = [f"path: {json.dumps(out.resolve().as_posix())}"]
    yaml += [f"{p}: images/{p}" for p in PARTS]
    yaml += ["names:"] + [f"  {k}: {json.dumps(n)}" for k, n in enumerate(names)]
    (out / "data.yaml").write_text("\n".join(yaml) + "\n")
    summary = {
        "names": names,
        "sessions": parts,
        "images": dict(images),
        "objects": {p: dict(c) for p, c in objects.items()},
        "settings": {"val_share": val_share, "seed": seed, "min_pixels": min_pixels},
    }
    (out / "parts.json").write_text(json.dumps(summary, indent=1))
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", type=Path, default=Path("datasets/sanpo"))
    p.add_argument("--out", type=Path, default=Path("datasets/sanpo_yolo"))
    p.add_argument("--val", type=float, default=0.1, help="share of training sessions held out")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--min-pixels", type=int, default=MIN_PIXELS)
    a = p.parse_args(argv)
    if not (a.root / "labelmap.json").exists():
        raise SystemExit(f"no SANPO at {a.root}: run tools/fetch_datasets.sh sanpo first")
    try:
        s = convert(a.root, a.out, a.val, a.seed, a.min_pixels)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    for part in PARTS:
        counts = ", ".join(f"{n} {c}" for n, c in sorted(s["objects"][part].items()))
        print(f"{part}: {len(s['sessions'][part])} sessions, {s['images'].get(part, 0)} images; "
              f"{counts or 'no objects'}")  # fmt: skip
    print(f"written to {a.out}; train with data={a.out / 'data.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
