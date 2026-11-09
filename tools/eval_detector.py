"""Score object detectors on the SANPO test part, the same way for every model (#49, #51).

    python3 tools/eval_detector.py --data datasets/sanpo_yolo --weights yolo26n.pt yoloe-26n-mana.pt
    python3 tools/eval_detector.py --data datasets/sanpo_yolo --weights runs/yolo26s/best.pt \\
        --imgsz 640 --json docs/eval/yolo26s.json

--data is the folder tools/sanpo_to_yolo.py writes. Each frame goes through
mana_perception.detection.Detector.detect_image, the call the ROS node and the phone demo make, so
the scores and times are those of the detector MANA runs.

Zero-shot models (YOLO26n on COCO, the demo's open-vocabulary YOLOE-26) do not use SANPO's class
names, so a fixed table, TO_SANPO, says which of their names count as which SANPO class. A model
fine-tuned on SANPO already uses SANPO's names. Only the SANPO classes a model can name are scored
for it; the others are listed as "not named". A prediction that overlaps a box of a neighbouring
class (a "person" on a SANPO "rider") is neither right nor wrong and is left out.

For each class: the objects in the test part, AP at IoU 0.5 (all-point, as VOC and Ultralytics
compute it), and the precision and recall at --conf, the threshold the node uses. For stairs, also
the recall per session: the share of each test walk's stairs frames in which a staircase was found,
averaged over the walks, because neighbouring frames of one walk are near copies. The time per
frame is the median on this machine, after a warm-up.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "mana_perception"))

IOU = 0.5
WARMUP = 3
LOW_CONF = 0.01  # the model is run this low so AP sees the whole precision-recall curve

# A zero-shot model's name -> the SANPO class it counts as. Names not here are not scored.
# bicycle and motorcycle are left out: whether SANPO's "vehicle" holds them is not stated.
TO_SANPO = {
    "person": "pedestrian",
    "car": "vehicle",
    "bus": "vehicle",
    "truck": "vehicle",
    "train": "vehicle",
    "stairs": "stairs",
    "door": "opening-door",
    "glass door": "opening-door",
    "pole": "pole",
    "pillar": "pole",
    "traffic light": "traffic light",
    "stop sign": "traffic sign",
    "dog": "animal",
    "cat": "animal",
    "bird": "animal",
    "horse": "animal",
}
# A prediction of the first class on a box of one of these is not counted either way.
NEIGHBOURS = {"pedestrian": {"rider"}, "vehicle": {"rider"}}


@dataclass(frozen=True)
class Box:
    cls: str
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float = 1.0


def iou(a: Box, b: Box) -> float:
    w = min(a.x2, b.x2) - max(a.x1, b.x1)
    h = min(a.y2, b.y2) - max(a.y1, b.y1)
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter
    return inter / union if union > 0 else 0.0


def read_labels(path: Path, names: list[str], width: float, height: float) -> list[Box]:
    """The boxes of one YOLO label file (class cx cy w h, 0 to 1) in pixels."""
    found = []
    if not path.exists():
        return found
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        k, cx, cy, w, h = line.split()
        cx, cy, w, h = float(cx) * width, float(cy) * height, float(w) * width, float(h) * height
        found.append(Box(names[int(k)], cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
    return found


def mapping(model_names, sanpo_names) -> dict[str, str]:
    """The model's name -> SANPO class, for the names that count as one."""
    sanpo = set(sanpo_names)
    out = {}
    for name in model_names:
        if name in sanpo:  # fine-tuned on SANPO: its names are SANPO's
            out[name] = name
        elif TO_SANPO.get(name) in sanpo:
            out[name] = TO_SANPO[name]
    return out


def match(preds: list[Box], truth: list[Box]):
    """(class, conf, right) for each prediction kept, and the truth boxes that were found.

    Highest confidence first, each prediction takes the unfound truth box of its class it overlaps
    most, if at IoU 0.5 or more. One that finds none but lies on a neighbouring class's box is
    dropped; any other is wrong."""
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
            out.append((p.cls, p.conf, True))
        elif any(t.cls in NEIGHBOURS.get(p.cls, ()) and iou(p, t) >= IOU for t in truth):
            continue
        else:
            out.append((p.cls, p.conf, False))
    return out, found


def average_precision(scored: list[tuple[float, bool]], positives: int) -> float:
    """Area under the precision-recall curve, precision made non-increasing (all-point)."""
    if positives == 0:
        return float("nan")
    scored = sorted(scored, key=lambda s: -s[0])
    tp = fp = 0
    recall, precision = [0.0], [1.0]
    for _conf, right in scored:
        tp += right
        fp += not right
        recall.append(tp / positives)
        precision.append(tp / (tp + fp))
    for i in range(len(precision) - 2, -1, -1):
        precision[i] = max(precision[i], precision[i + 1])
    return sum((recall[i] - recall[i - 1]) * precision[i] for i in range(1, len(recall)))


@dataclass
class Tally:
    positives: dict = field(default_factory=lambda: defaultdict(int))
    scored: dict = field(default_factory=lambda: defaultdict(list))  # class -> [(conf, right)]
    stairs: dict = field(default_factory=lambda: defaultdict(lambda: [0, 0]))  # session -> [hit, n]
    times: list = field(default_factory=list)

    def add(self, session: str, preds: list[Box], truth: list[Box], classes: set[str], conf: float):
        truth = [
            t for t in truth if t.cls in classes or any(t.cls in v for v in NEIGHBOURS.values())
        ]
        kept, _ = match(preds, truth)
        for t in truth:
            if t.cls in classes:
                self.positives[t.cls] += 1
        for cls, c, right in kept:
            self.scored[cls].append((c, right))
        if "stairs" in classes and any(t.cls == "stairs" for t in truth):
            _, found = match([p for p in preds if p.conf >= conf], truth)  # as the node sees it
            hit = any(f and t.cls == "stairs" for t, f in zip(truth, found, strict=True))
            self.stairs[session][0] += hit
            self.stairs[session][1] += 1

    def rows(self, classes: set[str], conf: float) -> list[dict]:
        out = []
        for cls in sorted(classes):
            n = self.positives[cls]
            at = [r for c, r in self.scored[cls] if c >= conf]
            tp = sum(at)
            out.append(
                {
                    "class": cls,
                    "objects": n,
                    "ap50": average_precision(self.scored[cls], n),
                    "precision": tp / len(at) if at else float("nan"),
                    "recall": tp / n if n else float("nan"),
                }
            )
        return out

    def stairs_by_session(self) -> dict:
        shares = [hit / n for hit, n in self.stairs.values() if n]
        return {
            "sessions": len(shares),
            "frames": sum(n for _, n in self.stairs.values()),
            "frames_found": sum(hit for hit, _ in self.stairs.values()),
            "mean_recall_per_session": statistics.mean(shares) if shares else float("nan"),
            "sessions_found_at_all": sum(1 for s in shares if s > 0),
        }


def test_frames(data: Path, part: str):
    """(session, image, label file) of every frame in a part, in order."""
    for image in sorted((data / "images" / part).iterdir()):
        if image.suffix.lower() in (".png", ".jpg", ".jpeg"):
            yield image.name.split("__")[0], image, data / "labels" / part / f"{image.stem}.txt"


def evaluate(detector, data: Path, part: str, conf: float, limit: int | None = None) -> dict:
    from PIL import Image

    sanpo = json.loads((data / "parts.json").read_text())["names"]
    to_sanpo = mapping(detector.model.names.values(), sanpo)
    classes = set(to_sanpo.values())
    tally = Tally()
    for i, (session, path, labels) in enumerate(test_frames(data, part)):
        if limit is not None and i >= limit:
            break
        with Image.open(path) as im:
            image = im.convert("RGB")
        start = time.perf_counter()
        found = detector.detect_image(image, conf=LOW_CONF)
        if i >= WARMUP:
            tally.times.append(time.perf_counter() - start)
        preds = [
            Box(to_sanpo[d.label], d.x1, d.y1, d.x2, d.y2, d.confidence)
            for d in found
            if d.label in to_sanpo
        ]
        truth = read_labels(labels, sanpo, image.width, image.height)
        tally.add(session, preds, truth, classes, conf)
    result = {
        "conf": conf,
        "imgsz": detector.imgsz,
        "classes": tally.rows(classes, conf),
        "not_named": sorted(set(sanpo) - classes),
        "ms_per_frame": 1000 * statistics.median(tally.times) if tally.times else float("nan"),
    }
    if "stairs" in classes:
        result["stairs"] = tally.stairs_by_session()
    return result


def table(name: str, r: dict) -> str:
    def f(v):
        return "n/a" if v != v else f"{v:.2f}"  # nan

    lines = [
        f"### {name} (imgsz {r['imgsz']}, conf {r['conf']}, {r['ms_per_frame']:.0f} ms/frame)",
        "",
        "| Class | Objects | AP50 | Precision | Recall |",
        "|---|---:|---:|---:|---:|",
    ]
    for c in r["classes"]:
        lines.append(
            f"| {c['class']} | {c['objects']} | {f(c['ap50'])} | "
            f"{f(c['precision'])} | {f(c['recall'])} |"
        )
    if "stairs" in r:
        s = r["stairs"]
        lines += [
            "",
            f"Stairs: found in {s['frames_found']} of {s['frames']} frames; "
            f"mean recall per session {f(s['mean_recall_per_session'])} over "
            f"{s['sessions']} sessions, {s['sessions_found_at_all']} found at least once.",
        ]
    lines += ["", "Not named by this model: " + (", ".join(r["not_named"]) or "none")]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("datasets/sanpo_yolo"))
    ap.add_argument("--weights", nargs="+", default=["yolo26n.pt"])
    ap.add_argument("--part", default="test")
    ap.add_argument("--imgsz", type=int, default=416)
    ap.add_argument("--conf", type=float, default=0.3, help="the node's threshold")
    ap.add_argument("--limit", type=int, help="score only the first N frames (a quick look)")
    ap.add_argument("--json", type=Path, help="also write the results here")
    a = ap.parse_args(argv)

    from mana_perception.detection import Detector

    results = {}
    for weights in a.weights:
        detector = Detector.load(weights, a.imgsz)
        if detector is None:
            print(f"cannot load {weights}: is ultralytics installed and the file there?")
            return 2
        results[weights] = evaluate(detector, a.data, a.part, a.conf, a.limit)
        print(table(weights, results[weights]) + "\n", flush=True)
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(results, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
