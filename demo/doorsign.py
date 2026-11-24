"""Which way a door opens, from the PUSH or PULL sign on it.

A wheelchair user can push a door open from the chair but often cannot pull one; a blind user
needs to know before reaching the handle. The plainest cue is the door's own sign, so when the
detector sees a door, this reads the text on and around it (RapidOCR, PaddleOCR's models on ONNX
Runtime, Apache-2.0) and looks for the words. A door with no readable sign stays "unknown":
handles and push plates are left for later, as a handle alone does not say which way.

Measured on TextOCR's real photos (tools/eval_door_signs.py, docs/results.md).
"""

from __future__ import annotations

import io
import re
import sys

WORDS = {"push": "push", "pull": "pull"}  # what a sign says -> which way the door opens
MIN_SCORE = 0.5  # the reader's own confidence in a word, below which it is not used
GROW = 0.25  # the door's box is grown by this share each way: signs sit on the frame too
THREADS = 2  # CPU threads for each of the reader's three models


def way(found: list[tuple[str, float]]) -> tuple[str | None, float]:
    """From the words read ((text, confidence)), "push" or "pull" and its confidence; None when
    neither, or both (a sign like "push button, pull lever" does not say)."""
    best: dict[str, float] = {}
    for text, score in found:
        if score < MIN_SCORE:
            continue
        for token in re.findall(r"[a-z]+", text.lower()):
            if token in WORDS:
                best[WORDS[token]] = max(best.get(WORDS[token], 0.0), score)
    if len(best) != 1:
        return None, 0.0
    ((side, score),) = best.items()
    return side, score


class DoorSign:
    """Reads a frame, or the part of it around a door, for PUSH or PULL."""

    def __init__(self, engine):
        self.engine = engine  # a RapidOCR(): image (H x W x 3 array) -> ([box, text, score]...)

    @classmethod
    def load(cls) -> DoorSign | None:
        try:
            import cv2
            from rapidocr_onnxruntime import RapidOCR

            # left to their defaults, its three ONNX models and OpenCV used 14.5 of a shared PC's
            # 28 cores, their idle threads spinning; at 2 threads each, 1.6 cores and 207 ms a
            # 640 x 480 frame, read once a second
            cv2.setNumThreads(1)
            return cls(RapidOCR(intra_op_num_threads=THREADS, inter_op_num_threads=1))
        except Exception as e:  # not installed
            print(f"door signs not read: {e}", file=sys.stderr)
            return None

    def words(self, image) -> list[tuple[str, float]]:
        result, _ = self.engine(image)
        return [(text, float(score)) for _, text, score in result or []]

    def read(self, jpeg: bytes, box=None) -> tuple[str | None, float]:
        """push or pull from the frame; box (x1, y1, x2, y2 in pixels): only around the door."""
        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(jpeg)) as im:
            im = im.convert("RGB")
            if box is not None:
                x1, y1, x2, y2 = box
                gx, gy = (x2 - x1) * GROW, (y2 - y1) * GROW
                im = im.crop(
                    (
                        max(0, x1 - gx),
                        max(0, y1 - gy),
                        min(im.width, x2 + gx),
                        min(im.height, y2 + gy),
                    )
                )
            image = np.asarray(im)
        return way(self.words(image))
