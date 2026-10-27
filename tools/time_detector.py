"""Time the object detector on this machine, as the ROS node and the phone demo run it.

    python3 tools/time_detector.py                                  # yolo26n.pt at 480
    python3 tools/time_detector.py --weights yoloe-26n-mana.pt --imgsz 480 416
    python3 tools/time_detector.py --images datasets/coco/val2017 --frames 50

Each frame goes through mana_perception.detection.Detector.detect_image, the same call the node
makes. The first frames are a warm-up and are not counted. Prints the median and the 90th
percentile time per frame, and the frames per second that the median allows. Without --images it
uses one picture from the Ultralytics package (a street with a bus and people).
"""

from __future__ import annotations

import argparse
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "mana_perception"))

from mana_perception.detection import Detector  # noqa: E402


def images(folder: Path | None, count: int):
    """count RGB pictures, from the folder or the one shipped with Ultralytics, repeated if few."""
    import numpy as np
    from PIL import Image

    if folder is None:
        import ultralytics

        paths = [Path(ultralytics.__file__).parent / "assets" / "bus.jpg"]
    else:
        paths = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".png"))
    if not paths:
        raise SystemExit(f"no .jpg or .png pictures in {folder}")
    pictures = [np.asarray(Image.open(p).convert("RGB")) for p in paths[:count]]
    return [pictures[i % len(pictures)] for i in range(count)]


def time_one(weights: str, imgsz: int, frames: list, warmup: int) -> dict:
    detector = Detector.load(weights, imgsz)
    if detector is None:
        raise SystemExit(f"cannot load {weights}: is ultralytics installed and the file there?")
    for frame in frames[:warmup]:
        detector.detect_image(frame)
    times = []
    for frame in frames[warmup:]:
        start = time.perf_counter()
        detector.detect_image(frame)
        times.append((time.perf_counter() - start) * 1000.0)
    times.sort()
    median = statistics.median(times)
    return {
        "median_ms": median,
        "p90_ms": times[int(0.9 * (len(times) - 1))],
        "fps": 1000.0 / median,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--weights", nargs="+", default=["yolo26n.pt"])
    ap.add_argument("--imgsz", nargs="+", type=int, default=[480])
    ap.add_argument("--images", type=Path, help="a folder of pictures (default: one bus picture)")
    ap.add_argument("--frames", type=int, default=40, help="timed frames per model and size")
    ap.add_argument("--warmup", type=int, default=5)
    args = ap.parse_args(argv)

    import torch

    frames = images(args.images, args.frames + args.warmup)
    print(f"{platform.processor() or platform.machine()}, {torch.get_num_threads()} threads, CPU")
    print(f"{'weights':22s} {'imgsz':>5s} {'median ms':>10s} {'p90 ms':>8s} {'fps':>6s}")
    for weights in args.weights:
        for imgsz in args.imgsz:
            r = time_one(weights, imgsz, frames, args.warmup)
            ms = f"{r['median_ms']:10.1f} {r['p90_ms']:8.1f}"
            print(f"{weights:22s} {imgsz:5d} {ms} {r['fps']:6.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
