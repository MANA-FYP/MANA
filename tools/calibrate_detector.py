"""How often the object detector is right at each confidence, on real SANPO walks (#87).

    python tools/fetch_sanpo.py --root datasets/sanpo --train 30 --test 30 --every 30
    python tools/calibrate_detector.py --root datasets/sanpo --weights yolo26n.pt

The decision core ignores a report whose trust is under min_trust (0.5), so a trust of 0.5 should
mean the report is right about half the time. The detector sends its confidence as the trust (times
the light factor). This runs the detector on SANPO-Real frames as the ROS node does (imgsz 416), and
counts a detection of an obstacle class as right when its box meets an object of that kind in the
frame's mask with IoU 0.5 or more (each object once, the most confident detection first). It
prints a reliability table (confidence bins against the share right) and the expected calibration
error (ECE), fits a logistic map from the confidence to the trust on the train split (Platt
scaling), and checks it on the test split. Detections under 40 pixels high are left out: SANPO's
masks leave small far objects out, and they would count as wrong.

The detections are kept in <root>/detections_<split>.csv; --again runs the detector anew.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "mana_perception"))

from mana_perception.detection import OBSTACLE_HEIGHT_M, Detector  # noqa: E402

# SANPO's classes -> the detector's labels that can be the same object
SAME = {
    "pedestrian": {"person"},
    "rider": {"person", "bicycle", "motorcycle"},
    "vehicle": {"car", "bus", "truck", "motorcycle", "bicycle"},
    "animal": {"dog"},
}
MIN_PIXELS = 400  # an object smaller than this in the mask is not counted
MIN_BOX_PX = 40  # a detection lower than this is left out
MIN_IOU = 0.5
BINS = np.linspace(0.0, 1.0, 11)


def objects(mask_rgb: np.ndarray, names: dict[int, str]) -> list[tuple[str, tuple]]:
    """(SANPO class, box x1 y1 x2 y2) of each object of a class in SAME in an RGB mask."""
    rgb = mask_rgb.astype(np.int64)
    ids = rgb[..., 0] * 65536 + rgb[..., 1] * 256 + rgb[..., 2]
    found = []
    values, sizes = np.unique(ids, return_counts=True)
    for value, size in zip(values.tolist(), sizes.tolist(), strict=True):
        label = names.get(value // 65536)
        if label not in SAME or size < MIN_PIXELS:
            continue
        ys, xs = np.nonzero(ids == value)
        found.append((label, (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)))
    return found


def iou(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def judge(detections, found) -> list[tuple[str, float, bool]]:
    """(label, confidence, right) for each detection of an obstacle class, the most confident
    first; an object can make only one detection right."""
    free = list(found)
    out = []
    for d in sorted(detections, key=lambda d: -d.confidence):
        if d.label not in OBSTACLE_HEIGHT_M or d.y2 - d.y1 < MIN_BOX_PX:
            continue
        box = (d.x1, d.y1, d.x2, d.y2)
        best = max(
            (o for o in free if d.label in SAME[o[0]]),
            key=lambda o: iou(box, o[1]),
            default=None,
        )
        right = best is not None and iou(box, best[1]) >= MIN_IOU
        if right:
            free.remove(best)
        out.append((d.label, d.confidence, right))
    return out


def reliability(conf: np.ndarray, right: np.ndarray) -> tuple[list[tuple], float]:
    """Rows (low, high, count, mean confidence, share right) for each bin with detections, and the
    expected calibration error: the gap between confidence and share right, weighted by count."""
    rows, ece = [], 0.0
    for lo, hi in zip(BINS[:-1], BINS[1:], strict=True):
        inside = (conf >= lo) & ((conf < hi) | (hi == 1.0))
        if inside.any():
            c, r = conf[inside].mean(), right[inside].mean()
            rows.append((lo, hi, int(inside.sum()), c, r))
            ece += inside.sum() / conf.size * abs(c - r)
    return rows, float(ece)


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def fit_platt(conf: np.ndarray, right: np.ndarray, steps: int = 200) -> tuple[float, float]:
    """(a, b) of trust = sigmoid(a * logit(confidence) + b), by Newton's method on the log loss."""
    x, y = logit(conf), right.astype(float)
    a, b = 1.0, 0.0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(a * x + b)))
        g = np.array([np.sum((p - y) * x), np.sum(p - y)])
        w = p * (1 - p)
        h = np.array([[np.sum(w * x * x), np.sum(w * x)], [np.sum(w * x), np.sum(w)]])
        h += 1e-9 * np.eye(2)
        a, b = np.array([a, b]) - np.linalg.solve(h, g)
    return float(a), float(b)


def trust(conf, a: float, b: float):
    return 1 / (1 + np.exp(-(a * logit(np.asarray(conf, dtype=float)) + b)))


def fit_isotonic(conf: np.ndarray, right: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The share right as a non-decreasing function of the confidence (pool adjacent violators):
    knots (mean confidence of each block) and the share right there."""
    order = np.argsort(conf)
    blocks = []  # [sum of right, count, sum of confidence]
    for c, r in zip(conf[order], right[order].astype(float), strict=True):
        blocks.append([r, 1.0, c])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]:
            r2, n2, c2 = blocks.pop()
            blocks[-1] = [blocks[-1][0] + r2, blocks[-1][1] + n2, blocks[-1][2] + c2]
    x = np.array([b[2] / b[1] for b in blocks])
    y = np.array([b[0] / b[1] for b in blocks])
    return x, y


def trust_isotonic(conf, knots: tuple[np.ndarray, np.ndarray]):
    return np.interp(np.asarray(conf, dtype=float), *knots)


def frames(root: Path, split: str):
    """(image, mask) paths of every frame of a split on disk (tools/fetch_sanpo.py's layout)."""
    for mask in sorted((root / split).glob("*/*/segmentation_masks/*.png")):
        image = mask.parents[1] / "video_frames" / mask.name
        if image.exists():
            yield image, mask


def detect(root: Path, split: str, weights: str, imgsz: int) -> Path:
    """Run the detector on a split and keep what it found, judged, in a CSV."""
    import json

    from PIL import Image

    detector = Detector.load(weights, imgsz)
    if detector is None:
        raise SystemExit(f"cannot load the detector {weights}")
    names = {i: n for n, i in json.loads((root / "labelmap.json").read_text()).items()}
    out = root / f"detections_{split}.csv"
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame", "label", "confidence", "right"])
        for n, (image, mask) in enumerate(frames(root, split), 1):
            with Image.open(image) as im:
                rgb = im.convert("RGB")  # a PIL image: Ultralytics reads an array as BGR
            with Image.open(mask) as m:
                found = objects(np.asarray(m.convert("RGB")), names)
            frame = "/".join(image.parts[-4:])
            for label, conf, right in judge(detector.detect_image(rgb, 0.05), found):
                w.writerow([frame, label, f"{conf:.4f}", int(right)])
            if n % 50 == 0:
                print(f"{split}: {n} frames", flush=True)
    return out


def load(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return (
        np.array([float(r["confidence"]) for r in rows]),
        np.array([r["right"] == "1" for r in rows]),
    )


def table(conf: np.ndarray, right: np.ndarray, label: str) -> str:
    rows, ece = reliability(conf, right)
    lines = [f"{label}: {conf.size} detections, ECE {ece:.3f}", "| Bin | n | mean | right |"]
    lines += [f"| {lo:.1f}-{hi:.1f} | {n} | {c:.2f} | {r:.2f} |" for lo, hi, n, c, r in rows]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", type=Path, default=Path("datasets/sanpo"))
    p.add_argument("--weights", default="yolo26n.pt")
    p.add_argument("--imgsz", type=int, default=416, help="as the ROS node runs it")
    p.add_argument("--again", action="store_true", help="run the detector again")
    a = p.parse_args(argv)
    data = {}
    for split in ("train", "test"):
        path = a.root / f"detections_{split}.csv"
        if a.again or not path.exists():
            detect(a.root, split, a.weights, a.imgsz)
        data[split] = load(path)
    fa, fb = fit_platt(*data["train"])
    knots = fit_isotonic(*data["train"])
    conf, right = data["test"]
    print(table(conf, right, "test, confidence as the trust"))
    print()
    print(table(trust(conf, fa, fb), right, f"test, trust = sigmoid({fa:.3f} logit(c) + {fb:.3f})"))
    print()
    print(table(trust_isotonic(conf, knots), right, "test, trust from isotonic regression"))
    half = 1 / (1 + math.exp(fb / fa))  # the confidence that maps to a trust of 0.5
    iso_half = float(np.interp(0.5, knots[1], knots[0]))
    print(f"\nA trust of 0.5 is a confidence of {half:.2f} (Platt), {iso_half:.2f} (isotonic).")
    print("isotonic knots (confidence, trust):")
    print(" ".join(f"({x:.2f}, {y:.2f})" for x, y in zip(*knots, strict=True)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
