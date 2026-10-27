"""Tests for the detector code shared by the phone demo and the ROS detector node: the two use the
same functions, ROS images become RGB arrays, and the field of view comes from the camera info."""

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "mana_perception"))

from demo import vision  # noqa: E402
from mana_perception import detection  # noqa: E402


def test_the_phone_and_ros_use_the_same_detector_and_maths():
    for name in ("Detector", "Detection", "to_obstacles", "distance_m", "bearing_rad"):
        assert getattr(vision, name) is getattr(detection, name)
    assert vision.OBSTACLE_HEIGHT_M is detection.OBSTACLE_HEIGHT_M


def pixels(encoding, rows=2, cols=3, pad=0):
    """A tiny image whose pixel (r, c) is (r, c, 9) in RGB, packed as the encoding asks."""
    np = pytest.importorskip("numpy")
    rgb = np.array([[[r, c, 9] for c in range(cols)] for r in range(rows)], dtype=np.uint8)
    channels, bgr = detection.CHANNELS[encoding]
    data = rgb[..., ::-1] if bgr else rgb
    if channels == 4:
        alpha = np.full((rows, cols, 1), 255, dtype=np.uint8)
        data = np.concatenate([data, alpha], axis=2)
    rows_bytes = data.reshape(rows, cols * channels)
    padded = np.concatenate([rows_bytes, np.zeros((rows, pad), dtype=np.uint8)], axis=1)
    return rgb, padded.tobytes(), cols * channels + pad


@pytest.mark.parametrize("encoding", ["rgb8", "bgr8", "rgba8", "bgra8"])
def test_ros_colour_images_become_rgb_arrays_even_with_padded_rows(encoding):
    np = pytest.importorskip("numpy")
    rgb, data, step = pixels(encoding, pad=4)
    out = detection.to_rgb(encoding, 2, 3, step, data)
    assert out.shape == (2, 3, 3) and out.flags["C_CONTIGUOUS"]
    assert np.array_equal(out, rgb)


def test_an_unsupported_encoding_gives_none():
    assert detection.to_rgb("mono8", 2, 3, 3, b"\0" * 6) is None


def test_the_field_of_view_comes_from_the_focal_length():
    fx = 320 / math.tan(math.radians(87.0) / 2.0)
    assert detection.hfov_from(fx, 640) == pytest.approx(math.radians(87.0))


def test_the_bearing_uses_the_cameras_own_field_of_view():
    box = detection.Detection("person", 0.9, 600, 100, 640, 400)  # at the right edge
    narrow = detection.to_obstacles([box], 640, 480, 500, hfov_rad=math.radians(40))[0]
    wide = detection.to_obstacles([box], 640, 480, 500, hfov_rad=math.radians(87))[0]
    assert narrow.bearing_rad < 0 and wide.bearing_rad < narrow.bearing_rad  # right is negative
    assert wide.distance_m == narrow.distance_m  # the distance needs only the focal length


def test_detect_image_and_detect_give_the_same_boxes():
    np = pytest.importorskip("numpy")
    image = pytest.importorskip("PIL.Image")
    from types import SimpleNamespace

    class Box:
        xyxy = [SimpleNamespace(tolist=lambda: [1.0, 2.0, 11.0, 22.0])]
        cls, conf = [0], [0.8]

    class Model:
        def __call__(self, frame, imgsz, conf, verbose):
            self.seen = np.asarray(frame).shape
            return [SimpleNamespace(names={0: "person"}, boxes=[Box()])]

    det = detection.Detector(Model(), imgsz=320)
    rgb = np.zeros((48, 64, 3), dtype=np.uint8)
    from_array = det.detect_image(rgb)
    assert det.model.seen == (48, 64, 3)
    buf = __import__("io").BytesIO()
    image.fromarray(rgb).save(buf, "JPEG")
    from_jpeg, w, h = det.detect(buf.getvalue())
    assert from_array == from_jpeg and (w, h) == (64, 48)
