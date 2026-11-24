"""Depth estimated from the image alone, as the phone demo makes it, for SANPO frames that have
stereo depth: Depth Anything V2 (Metric-Indoor-Small) on each frame, saved as
<camera>/depth_dav2/<frame>.npy (metres, float16). Train the stairs segmentation on it with
tools/train_stair_seg.py --depth depth_dav2 --only-with depth_npy, for the depth a phone has.

    python3 tools/estimate_sanpo_depth.py datasets/sanpo

Needs torch, transformers and a GPU for a reasonable time (about 25 minutes for 23,645 frames on
an RTX 4000 Ada). Frames already done are skipped.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

MODEL = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"  # as demo/depth.py


def todo(root: Path) -> list[tuple[Path, Path]]:
    """(frame, where its estimated depth goes) for frames with stereo depth and none estimated."""
    found = []
    for stereo in sorted(root.glob("train/*/*/depth_npy/*.npy")):
        cam = stereo.parent.parent
        out = cam / "depth_dav2" / stereo.name
        if not out.exists():
            found.append((cam / "video_frames" / (stereo.stem + ".png"), out))
    return found


def main(argv: list[str] | None = None) -> int:
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path("datasets/sanpo")
    jobs = todo(root)
    print(f"frames: {len(jobs)}", flush=True)
    on = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoImageProcessor.from_pretrained(MODEL)
    model = AutoModelForDepthEstimation.from_pretrained(MODEL).eval().to(on)

    def load(job):
        frame, out = job
        return Image.open(frame).convert("RGB").resize((640, 360)), out

    with ThreadPoolExecutor(8) as ex:
        for i in range(0, len(jobs), 16):
            batch = list(ex.map(load, jobs[i : i + 16]))
            size = {"height": 266, "width": 476}
            inputs = processor(images=[b[0] for b in batch], return_tensors="pt", size=size)
            with torch.inference_mode():
                depth = model(**inputs.to(on)).predicted_depth.float().cpu().numpy()
            for d, (_, out) in zip(depth, batch, strict=True):
                out.parent.mkdir(exist_ok=True)
                np.save(out, d.astype(np.float16))
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
