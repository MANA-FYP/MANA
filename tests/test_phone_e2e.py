"""tools/phone_e2e.py: the video Chrome plays as the phone's camera. The run itself needs Chrome
and the demo server, so it is not part of the tests; this checks the conversion."""

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import phone_e2e  # noqa: E402

# a mark, not a module-level importorskip: CI's launch_testing plugin turns that into a skipped
# session (#139)
needs_cv2 = pytest.mark.skipif(importlib.util.find_spec("cv2") is None, reason="needs OpenCV")


def test_an_mjpeg_is_played_as_it_is(tmp_path):
    video = tmp_path / "walk.mjpeg"
    video.write_bytes(b"\xff\xd8\xff\xd9")
    assert phone_e2e.to_mjpeg(video, tmp_path / "out.mjpeg") == video


@needs_cv2
def test_a_video_becomes_jpegs_at_30_a_second(tmp_path):
    import cv2
    import numpy as np

    src = tmp_path / "walk.avi"
    out = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"MJPG"), 5, (320, 240))
    for i in range(10):  # 2 s at 5 frames a second
        out.write(np.full((240, 320, 3), 25 * i, np.uint8))
    out.release()
    mjpeg = phone_e2e.to_mjpeg(src, tmp_path / "camera.mjpeg", width=160)
    data = mjpeg.read_bytes()
    assert data.count(b"\xff\xd8") == 60  # each frame six times: 2 s at 30 a second
    first = cv2.imdecode(np.frombuffer(data[: data.index(b"\xff\xd9") + 2], np.uint8), 1)
    assert first.shape[:2] == (120, 160)
