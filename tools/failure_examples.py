"""Pictures of a detector's failures on the SANPO test part, for the evaluation report (#51).

    python3 tools/failure_examples.py --data datasets/sanpo-stairs-yolo \\
        --weights runs/detect/train/weights/best.pt --imgsz 416 --out datasets/failures

For each class asked for (stairs, opening-door and pole by default) it finds, at --conf (0.3, the
lowest confidence the node keeps):

- misses: near boxes of that class (at least --min-height of the frame's height) that no prediction
  found, the largest first, since a large staircase missed is the plainest failure;
- false boxes: predictions of that class that match no box, the most confident first.

A miss and a false box mean exactly what they mean in tools/eval_detector.py: the same matching
(IoU 0.5, same class, highest confidence first), the same neighbour rule and the same size band. At
most one picture per walk and kind, since neighbouring frames of a walk are near copies.

Each picture shows the frame scaled to --width pixels, the class's true boxes in green (a missed one
drawn thicker), and the model's boxes of that class in orange with their confidence. index.json
lists every picture: walk, frame, class, kind, box and confidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src" / "mana_perception"))

from eval_detector import (  # noqa: E402
    IOU,
    LOW_CONF,
    NEIGHBOURS,
    Box,
    iou,
    mapping,
    read_labels,
    test_frames,
)

GREEN, ORANGE = (40, 200, 60), (255, 140, 0)
CLASSES = ("stairs", "opening-door", "pole")


def judge(preds: list[Box], truth: list[Box], ignore: list[Box] = (), min_px: float = 0.0):
    """Each prediction match() scores, as (box, right), and the truth boxes found: match()'s own
    loop, keeping the boxes, so a wrong box here is a wrong box in the scores."""
    found = [False] * len(truth)
    out = []
    for p in sorted(preds, key=lambda b: -b.conf):
        best, best_iou = -1, IOU
        for i, t in enumerate(truth):
            if t.cls == p.cls and not found[i]:
                v = iou(p, t)
                if v >= best_iou:
                    best, best_iou = i, v
        if best >= 0:
            found[best] = True
            out.append((p, True))
        elif any(t.cls in NEIGHBOURS.get(p.cls, ()) and iou(p, t) >= IOU for t in truth):
            continue
        elif p.height < min_px or any(t.cls == p.cls and iou(p, t) >= IOU for t in ignore):
            continue
        else:
            out.append((p, False))
    return out, found


def failures(preds: list[Box], truth: list[Box], cls: str, conf: float, min_px: float):
    """The near boxes of cls that were missed, and the predictions of cls that are false, in one
    frame, scored as eval_detector scores the near band."""
    truth = [t for t in truth if t.cls == cls or t.cls in NEIGHBOURS.get(cls, ())]
    ignore = [t for t in truth if t.height < min_px]
    near = [t for t in truth if t.height >= min_px]
    kept = [p for p in preds if p.cls == cls and p.conf >= conf]
    judged, found = judge(kept, near, ignore, min_px)
    missed = [t for t, f in zip(near, found, strict=True) if t.cls == cls and not f]
    return missed, [p for p, right in judged if not right]


def pick(found: list[dict], n: int) -> list[dict]:
    """The n strongest examples, at most one per walk."""
    out, walks = [], set()
    for f in sorted(found, key=lambda f: -f["strength"]):
        if f["walk"] not in walks:
            out.append(f)
            walks.add(f["walk"])
        if len(out) == n:
            break
    return out


def draw(image, example: dict, preds: list[Box], truth: list[Box], width: int):
    """The frame with the class's true boxes (green) and predictions (orange), scaled."""
    from PIL import ImageDraw

    scale = width / image.width
    out = image.resize((width, round(image.height * scale)))
    pen = ImageDraw.Draw(out)
    cls = example["class"]

    def box(b: Box, colour, thick: int) -> None:
        pen.rectangle([b.x1 * scale, b.y1 * scale, b.x2 * scale, b.y2 * scale], outline=colour,
                      width=thick)  # fmt: skip

    target = example["box"]
    for t in truth:
        if t.cls == cls:
            missed = example["kind"] == "missed" and [t.x1, t.y1, t.x2, t.y2] == target
            box(t, GREEN, 5 if missed else 2)
    for p in preds:
        if p.cls == cls:
            box(p, ORANGE, 3)
            pen.text((p.x1 * scale + 3, max(0, p.y1 * scale - 12)), f"{p.conf:.2f}", fill=ORANGE)
    return out


def collect(detector, data: Path, part: str, classes, conf: float, min_height: float):
    """Every miss and false box of the classes in the part, with what is needed to draw it."""
    from PIL import Image

    sanpo = json.loads((data / "parts.json").read_text())["names"]
    to_sanpo = mapping(detector.model.names.values(), sanpo)
    found = []
    for walk, path, labels in test_frames(data, part):
        with Image.open(path) as im:
            image = im.convert("RGB")
        preds = [
            Box(to_sanpo[d.label], d.x1, d.y1, d.x2, d.y2, d.confidence)
            for d in detector.detect_image(image, conf=LOW_CONF)
            if d.label in to_sanpo
        ]
        truth = read_labels(labels, sanpo, image.width, image.height)
        for cls in classes:
            if cls not in to_sanpo.values():
                continue
            missed, false = failures(preds, truth, cls, conf, min_height * image.height)
            for kind, boxes in (("missed", missed), ("false", false)):
                for b in boxes:
                    found.append(
                        {
                            "walk": walk,
                            "frame": path.name,
                            "class": cls,
                            "kind": kind,
                            "box": [round(v, 1) for v in (b.x1, b.y1, b.x2, b.y2)],
                            "conf": round(b.conf, 3) if kind == "false" else None,
                            # a missed box by its share of the frame's height, a false one by conf
                            "strength": b.height / image.height if kind == "missed" else b.conf,
                        }
                    )
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--imgsz", type=int, default=416)
    ap.add_argument("--part", default="test")
    ap.add_argument("--classes", nargs="+", default=list(CLASSES))
    ap.add_argument("--conf", type=float, default=0.3, help="the lowest confidence the node keeps")
    ap.add_argument("--min-height", type=float, default=0.1, help="near: box height share")
    ap.add_argument("-n", type=int, default=4, help="pictures per class and kind")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--out", type=Path, default=Path("datasets/failures"))
    a = ap.parse_args(argv)

    from mana_perception.detection import Detector
    from PIL import Image

    detector = Detector.load(a.weights, a.imgsz)
    if detector is None:
        print(f"cannot load {a.weights}: is ultralytics installed and the file there?")
        return 2
    found = collect(detector, a.data, a.part, a.classes, a.conf, a.min_height)
    sanpo = json.loads((a.data / "parts.json").read_text())["names"]
    to_sanpo = mapping(detector.model.names.values(), sanpo)
    a.out.mkdir(parents=True, exist_ok=True)
    index = []
    for cls in a.classes:
        for kind in ("missed", "false"):
            every = [f for f in found if f["class"] == cls and f["kind"] == kind]
            walks = len({f["walk"] for f in every})
            print(f"{cls}, {kind}: {len(every)} boxes in {walks} walks")
            for k, ex in enumerate(pick(every, a.n), 1):
                image_path = a.data / "images" / a.part / ex["frame"]
                with Image.open(image_path) as im:
                    image = im.convert("RGB")
                labels = a.data / "labels" / a.part / f"{Path(ex['frame']).stem}.txt"
                truth = read_labels(labels, sanpo, image.width, image.height)
                preds = [
                    Box(to_sanpo[d.label], d.x1, d.y1, d.x2, d.y2, d.confidence)
                    for d in detector.detect_image(image, conf=a.conf)
                    if d.label in to_sanpo
                ]
                name = f"{cls}-{kind}-{k}.jpg"
                draw(image, ex, preds, truth, a.width).save(a.out / name, quality=80)
                index.append({**{k2: v for k2, v in ex.items() if k2 != "strength"}, "file": name})
    (a.out / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(f"{len(index)} pictures in {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
