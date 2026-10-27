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
    assert vision.OBSTACLE_WIDTH_M is detection.OBSTACLE_WIDTH_M


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


def test_the_phone_and_ros_share_one_trust_rule_for_the_light():
    from demo import middleware

    assert middleware.camera_trust_factor is detection.camera_trust_factor
    assert vision.brightness is detection.brightness


@pytest.mark.parametrize("level", [10, 30, 60, 200])
def test_the_same_frame_gives_the_same_trust_on_the_phone_and_in_ros(level):
    np = pytest.importorskip("numpy")
    image = pytest.importorskip("PIL.Image")
    from demo.server import brightness_of

    rgb = np.full((480, 640, 3), level, dtype=np.uint8)
    buf = __import__("io").BytesIO()
    image.fromarray(rgb).save(buf, "JPEG")
    phone = detection.camera_trust_factor(brightness_of(buf.getvalue()))
    ros = detection.camera_trust_factor(detection.brightness(rgb))
    assert phone == pytest.approx(ros, abs=0.01)
    assert detection.brightness(rgb) == pytest.approx(level / 255, abs=1e-6)


def test_brightness_weighs_colours_as_pil_does():
    np = pytest.importorskip("numpy")
    image = pytest.importorskip("PIL.Image")
    rng = np.random.default_rng(0)
    rgb = rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)
    pil = np.asarray(image.fromarray(rgb).convert("L"), dtype=np.float64).mean() / 255
    assert detection.brightness(rgb) == pytest.approx(pil, abs=0.02)


def person(x1, x2, y1=0, y2=480, confidence=0.9):
    return detection.Detection("person", confidence, x1, y1, x2, y2)


def test_a_person_cut_by_the_frame_keeps_coming_closer_with_the_width():
    # the full-height box is the same for every close person, so its height says about 1.77 m
    by_height = detection.distance_m(480, 1.7, 500)
    far, near = (
        detection.to_obstacles([person(220, 420 + extra)], 640, 480, 500)[0] for extra in (0, 200)
    )
    assert far.distance_m == pytest.approx(500 * 0.4 / 200)  # 1.0 m, from 0.4 m shoulders
    assert near.distance_m == pytest.approx(500 * 0.4 / 400)  # 0.5 m: inside the 0.8 m stop
    assert near.distance_m < far.distance_m < by_height


def test_a_person_with_legs_below_the_frame_is_cut_too():
    # a real YOLO26n box from the Ultralytics portrait: it stops 6 px above the bottom edge
    legs_cut = detection.Detection("person", 0.89, 746, 42, 1154, 714)
    assert detection.cut_by_frame(legs_cut, 720)
    r = detection.to_obstacles([legs_cut], 1280, 720, 692)[0]
    assert r.distance_m == pytest.approx(692 * 0.4 / 408)  # the width: nearer than the height's
    assert not detection.cut_by_frame(detection.Detection("person", 0.9, 0, 20, 10, 690), 720)


def test_a_box_that_is_not_cut_and_other_classes_keep_the_height_distance():
    whole = detection.to_obstacles([person(300, 340, y1=100, y2=350)], 640, 480, 500)[0]
    assert whole.distance_m == pytest.approx(500 * 1.7 / 250)
    car = detection.Detection("car", 0.9, 0, 0, 640, 480)  # cut, but no typical width
    assert detection.to_obstacles([car], 640, 480, 500)[0].distance_m == pytest.approx(
        500 * 1.5 / 480
    )
    wide = person(0, 640)  # cut and as wide as the frame: right in front, 0.31 m
    assert detection.to_obstacles([wide], 640, 480, 500)[0].distance_m == pytest.approx(0.3125)
