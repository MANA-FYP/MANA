"""Obstacle distance from the box height against a monocular depth model, on real SANPO walks (#88).

    python tools/depth_vs_box.py --root datasets/sanpo-depth --sessions 141 --frames 6
    python tools/depth_vs_box.py --root datasets/sanpo-depth --rescore     # the saved boxes again

For SANPO-Real test sessions (left image of whichever camera, chest or head, has masks) it fetches
frames that show a person, a vehicle or another obstacle class, with their masks and SANPO's depth
maps. The detector runs on each frame; each box is matched to the object's mask (the same kind of
object, IoU 0.5 or more), and the object's true distance is the median depth over its mask. Each
box then gets:

- box: the distance from its height and a typical height for the class (mana_perception.detection,
  with the camera's own focal length),
- the median depth of Depth Anything V2 Small (metric; indoor and outdoor versions) over the middle
  half of the box, as demo/depth.py takes it, in the model's own metres: SANPO does not give the
  camera's height above the floor, which the demo uses to scale the depth,
- nearer: the nearer of box and indoor, which the demo keeps.

Prints the error by distance band, by class and for boxes cut by the frame, with the share read
more than 20 percent too far (the unsafe side), and the time per frame of each depth model, and
writes every matched box to <root>/depth_vs_box.csv. SANPO's depth is itself from stereo, so it is
a reference, not ground truth; it is best close up.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import statistics
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src" / "mana_perception"), str(ROOT / "tools"), str(ROOT)]

import fetch_sanpo as fs  # noqa: E402
from mana_perception.detection import (  # noqa: E402
    Detector,
    cut_by_frame,
    to_obstacles,
)

from demo.depth import DepthModel, Seen, box_distance  # noqa: E402
from mana_perception import depth as dp  # noqa: E402

# SANPO's cameras by model: a ZED 2i on the chest, a ZED Mini on the head (the SANPO paper)
MODEL_OF = {"camera_chest": "CAMERA_MODEL_ZED_2I", "camera_head": "CAMERA_MODEL_ZED_M"}
MODELS = {
    "indoor": "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf",
    "outdoor": "depth-anything/Depth-Anything-V2-Metric-Outdoor-Small-hf",
}
BANDS = ((0, 2), (2, 4), (4, 8), (8, 100))
MIN_PIXELS = 400  # an object smaller than this in the mask is not used
MIN_IOU = 0.5
# SANPO's classes -> the detector's labels that can be the same object
SAME = {
    "pedestrian": {"person"},
    "rider": {"person", "bicycle", "motorcycle"},
    "vehicle": {"car", "bus", "truck", "motorcycle"},
    "animal": {"dog"},
}


def read_depth(raw: bytes) -> np.ndarray:
    """A SANPO depth map: gzipped float16, the first two values its rows and columns, in metres."""
    a = np.frombuffer(gzip.decompress(raw), np.float16)
    rows, cols = int(a[0]), int(a[1])
    return a[2:].reshape(rows, cols).astype(np.float32)


def objects(mask_rgb: np.ndarray, names: dict[int, str]) -> list[tuple[str, np.ndarray, tuple]]:
    """(SANPO class, pixels, box x1 y1 x2 y2) of each object of an obstacle class in an RGB
    mask."""
    rgb = mask_rgb.astype(np.int64)
    ids = rgb[..., 0] * 65536 + rgb[..., 1] * 256 + rgb[..., 2]
    found = []
    for value in np.unique(ids).tolist():
        label = names.get(value // 65536)
        if label not in SAME:
            continue
        pixels = ids == value
        if pixels.sum() < MIN_PIXELS:
            continue
        ys, xs = np.nonzero(pixels)
        found.append((label, pixels, (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)))
    return found


def iou(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def true_distance(pixels: np.ndarray, depth: np.ndarray) -> float | None:
    """The median depth over an object's mask, the mask scaled to the depth map's size."""
    rows, cols = depth.shape
    r = (np.arange(rows) * pixels.shape[0] / rows).astype(int)
    c = (np.arange(cols) * pixels.shape[1] / cols).astype(int)
    z = depth[pixels[r][:, c]]
    z = z[np.isfinite(z) & (z > 0)]
    return float(np.median(z)) if z.size >= 20 else None


def fetch_frames(root: Path, sessions: int, per_session: int, fetch=fs.get) -> list[Path]:
    """The masks of up to per_session frames with an obstacle, in each of the test sessions,
    with their image and depth map next to them."""
    for name in fs.META:
        fs.download(fs.file_url(f"{fs.PREFIX}/{name}"), root / name, fetch)
    ids = root / "splits" / fs.SPLITS["test"]
    fs.download(fs.file_url(f"{fs.PREFIX}/sanpo-real/splits/{fs.SPLITS['test']}"), ids, fetch)
    names = class_names(root)
    kept = []
    for session in fs.pick(fs.parse_ids(ids.read_text()), sessions):
        got = 0
        for camera in fs.CAMERAS:  # most sessions have masks for one camera only
            got += fetch_camera(root, session, camera, per_session - got, names, kept, fetch)
        print(f"{session}: {got} frames", flush=True)
    return kept


def fetch_camera(root, session, camera, wanted, names, kept, fetch) -> int:
    left = f"{fs.PREFIX}/sanpo-real/{session}/{camera}/left"
    try:
        masks = fs.list_names(f"{left}/segmentation_masks/", fetch)
    except fs.Missing:
        return 0
    got = 0
    if wanted > 0 and masks:
        frames = fs.frame_ids(masks, max(1, len(masks) // (4 * wanted)))
        for frame in frames:
            if got == wanted:
                break
            mask = fs.local_path(root, "test", f"{left}/segmentation_masks/{frame}.png")
            try:
                fs.download(fs.file_url(f"{left}/segmentation_masks/{frame}.png"), mask, fetch)
                if not objects(load_rgb(mask), names):
                    continue
                for kind, ext in (("video_frames", "png"), ("depth_maps", "float16.gz")):
                    name = f"{left}/{kind}/{frame}.{ext}"
                    fs.download(fs.file_url(name), fs.local_path(root, "test", name), fetch)
            except fs.Missing:
                continue
            kept.append(mask)
            got += 1
    return got


def class_names(root: Path) -> dict[int, str]:
    import json

    return {i: n for n, i in json.loads((root / "labelmap.json").read_text()).items()}


def load_rgb(path: Path) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def focal(root: Path, session: str, camera: str) -> tuple[float, float, float, float]:
    import json

    url = fs.file_url(f"{fs.PREFIX}/sanpo-real/{session}/description.json")
    dest = root / "test" / session / "description.json"
    fs.download(url, dest)
    for cam in json.loads(dest.read_text())["session_camera_details"]:
        if cam["model"] == MODEL_OF[camera]:
            p = cam["left_camera_params"]
            return p["fx"], p["fy"], p["cx"], p["cy"]
    raise fs.Missing(f"{session} {camera}")


def measure(root: Path, masks: list[Path], weights: str, imgsz: int) -> tuple[list[dict], dict]:
    from PIL import Image

    detector = Detector.load(weights, imgsz)
    if detector is None:
        raise SystemExit(f"cannot load the detector {weights}")
    models = {k: DepthModel.load(v) for k, v in MODELS.items()}
    models = {k: m for k, m in models.items() if m is not None}
    names, rows, times = class_names(root), [], {k: [] for k in models}
    for mask_path in masks:
        session, camera, frame = mask_path.parts[-4], mask_path.parts[-3], mask_path.stem
        folder = mask_path.parents[1]
        fx, fy, cx, cy = focal(root, session, camera)
        image = load_rgb(folder / "video_frames" / f"{frame}.png")
        h, w = image.shape[:2]
        truth = read_depth((folder / "depth_maps" / f"{frame}.float16.gz").read_bytes())
        found = objects(load_rgb(mask_path), names)
        buf = io.BytesIO()
        Image.fromarray(image).save(buf, "JPEG", quality=90)  # as the phone sends frames
        jpeg = buf.getvalue()
        seen = {}
        for k, m in models.items():
            t = time.perf_counter()
            depth = m.estimate(jpeg)
            times[k].append(time.perf_counter() - t)
            seen[k] = Seen(depth, 1.0, None, cam=dp.Camera(fx, fy, cx, cy, 1.0, 0.0))
        for d in detector.detect_image(Image.fromarray(image)):  # RGB: an array is read as BGR
            box = to_obstacles([d], w, h, focal_px=fy)
            if not box:
                continue
            match = max(
                (o for o in found if d.label in SAME[o[0]]),
                key=lambda o: iou((d.x1, d.y1, d.x2, d.y2), o[2]),
                default=None,
            )
            if match is None or iou((d.x1, d.y1, d.x2, d.y2), match[2]) < MIN_IOU:
                continue
            true = true_distance(match[1], truth)
            if true is None:
                continue
            row = {
                "session": session,
                "camera": camera,
                "frame": frame,
                "label": d.label,
                "cut": cut_by_frame(d, h),
                "true_m": round(true, 2),
                "box_m": round(box[0].distance_m, 2),
            }
            for k, s in seen.items():
                est = box_distance(s, d, w, h)
                row[f"{k}_m"] = None if est is None else round(est, 2)
            rows.append(row)
    return rows, {k: statistics.median(v) for k, v in times.items() if v}


def errors(rows: list[dict], key: str) -> str:
    """The median error as a share of the true distance, the share within 20 percent, and the
    share read more than 20 percent too far (the unsafe side)."""
    ratio = [r[key] / r["true_m"] for r in rows if r.get(key) is not None]
    if not ratio:
        return "-"
    e = [abs(x - 1) for x in ratio]
    within = sum(x <= 0.2 for x in e) / len(e)
    far = sum(x > 1.2 for x in ratio) / len(ratio)
    return f"{statistics.median(e):.0%} ({within:.0%}, {far:.0%})"


def with_nearer(rows: list[dict], model: str = "indoor") -> list[dict]:
    """Each row with nearer_m: the nearer of the box's distance and the depth model's, as the
    demo keeps it (#88)."""
    for r in rows:
        depth = r.get(f"{model}_m")
        r["nearer_m"] = r["box_m"] if depth is None else min(r["box_m"], depth)
    return rows


def read_rows(path: Path) -> list[dict]:
    """The rows of a depth_vs_box.csv, with numbers as numbers."""
    rows = []
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            for k, v in r.items():
                if k.endswith("_m"):
                    r[k] = float(v) if v else None
            r["cut"] = r["cut"] == "True"
            rows.append(r)
    return rows


def table(rows: list[dict], methods: list[str]) -> str:
    head = "| Boxes | n | " + " | ".join(methods) + " |"
    lines = [head, "|---|---|" + "---|" * len(methods)]

    def line(name, group):
        cells = " | ".join(errors(group, f"{m}_m") for m in methods)
        lines.append(f"| {name} | {len(group)} | {cells} |")

    line("all", rows)
    for lo, hi in BANDS:
        line(
            f"{lo} to {hi} m" if hi < 100 else f"{lo} m and more",
            [r for r in rows if lo <= r["true_m"] < hi],
        )
    for label in sorted(
        {r["label"] for r in rows}, key=lambda x: -sum(r["label"] == x for r in rows)
    )[:4]:
        line(label, [r for r in rows if r["label"] == label])
    line("cut by the frame", [r for r in rows if r["cut"]])
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", type=Path, default=Path("datasets/sanpo-depth"))
    p.add_argument("--sessions", type=int, default=20, help="test sessions, spread over the list")
    p.add_argument("--frames", type=int, default=8, help="frames with an obstacle per session")
    p.add_argument("--weights", default="yolo26n.pt")
    p.add_argument("--imgsz", type=int, default=480)
    p.add_argument("--rescore", action="store_true", help="score <root>/depth_vs_box.csv again")
    a = p.parse_args(argv)
    saved = a.root / "depth_vs_box.csv"
    if a.rescore:
        rows, times = read_rows(saved), {}
    else:
        masks = fetch_frames(a.root, a.sessions, a.frames)
        rows, times = measure(a.root, masks, a.weights, a.imgsz)
        if not rows:
            print("no box matched an object", file=sys.stderr)
            return 1
        with saved.open("w", newline="") as f:
            out = csv.DictWriter(f, fieldnames=list(rows[0]))
            out.writeheader()
            out.writerows(rows)
    models = [k[:-2] for k in rows[0] if k.endswith("_m") and k not in ("true_m", "box_m")]
    frames = len({(r["session"], r.get("camera"), r["frame"]) for r in rows})
    print(f"\n{len(rows)} boxes in {frames} frames. Median error (share within 20%, share read")
    print("more than 20% too far); nearer: the nearer of box and indoor, as the demo keeps it:\n")
    print(table(with_nearer(rows), ["box", *models, "nearer"]))
    if times:
        print(
            "\nDepth model time per frame: "
            + ", ".join(f"{k} {v * 1000:.0f} ms" for k, v in times.items())
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
