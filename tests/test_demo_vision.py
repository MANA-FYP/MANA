"""Tests for the camera vision maths: bearings, distances, which detections count, and the
loaders returning None when a model is not installed. No model is needed."""

import importlib
import math

import pytest

from demo import vision
from demo.vision import (
    Detection,
    Detector,
    GroundClassifier,
    bearing_rad,
    calibrate_focal,
    distance_m,
    focal_from_fov,
    stair_ground,
    to_obstacles,
)


def box(label, height, center_x=320.0, conf=0.9):
    return Detection(label, conf, center_x - 50, 100.0, center_x + 50, 100.0 + height)


def test_distance_follows_the_size_of_the_box():
    focal = 800.0
    assert distance_m(400.0, 1.7, focal) == pytest.approx(3.4)
    assert distance_m(800.0, 1.7, focal) == pytest.approx(1.7)  # twice the size, half the distance


def test_calibration_recovers_the_distance_it_was_made_at():
    focal = calibrate_focal(box_height_px=500.0, real_height_m=1.7, distance_m=2.0)
    assert distance_m(500.0, 1.7, focal) == pytest.approx(2.0)


def test_an_empty_box_has_no_distance():
    assert math.isinf(distance_m(0.0, 1.7, 800.0))


def test_straight_ahead_has_no_bearing_and_the_left_is_positive():
    assert bearing_rad(320.0, 640.0) == pytest.approx(0.0)
    assert bearing_rad(0.0, 640.0) > 0  # far left of the frame
    assert bearing_rad(640.0, 640.0) < 0  # far right of the frame
    assert bearing_rad(0.0, 640.0) == pytest.approx(-bearing_rad(640.0, 640.0))


def test_only_obstacle_classes_with_enough_confidence_count():
    found = to_obstacles(
        [box("person", 400.0), box("cat", 300.0), box("car", 300.0, conf=0.2)], 640.0, 480.0
    )
    assert [o.label for o in found] == ["person"]


def test_the_nearest_obstacle_comes_first():
    found = to_obstacles([box("person", 200.0), box("person", 600.0, center_x=100.0)], 640.0, 480.0)
    assert found[0].distance_m < found[1].distance_m


def test_a_larger_box_means_a_closer_person():
    near, far = (to_obstacles([box("person", h)], 640.0, 480.0)[0] for h in (600.0, 150.0))
    assert near.distance_m < far.distance_m


def test_focal_length_from_the_field_of_view():
    f = focal_from_fov(480.0, math.radians(60.0))
    assert f == pytest.approx(240.0 / math.tan(math.radians(30.0)))


def test_stairs_labels_map_to_stairs_and_the_rest_to_flat():
    assert stair_ground("stairs") == "stairs"
    assert stair_ground("to_stairs") == "stairs"
    assert stair_ground("level") == "flat"
    assert stair_ground("to_level") == "flat"


def test_the_detector_is_none_when_ultralytics_is_not_installed(monkeypatch):
    def refuse(name, *a, **k):
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", refuse)
    assert Detector.load("yolo26n.pt") is None


def test_the_classifier_is_none_when_the_model_file_is_missing():
    assert GroundClassifier.load("no/such/model.onnx") is None


def test_preprocess_gives_a_normalised_batch():
    np = pytest.importorskip("numpy")
    image = pytest.importorskip("PIL.Image").new("RGB", (64, 48), (128, 128, 128))
    x = vision.preprocess(image, size=32)
    assert x.shape == (1, 3, 32, 32) and x.dtype == np.float32


class FakeModel:
    """Stands in for an Ultralytics model and records what it was called with."""

    def __init__(self, weights):
        self.weights, self.calls = weights, []

    def __call__(self, image, **kwargs):
        from types import SimpleNamespace

        self.calls.append(kwargs)
        return [SimpleNamespace(names={}, boxes=[])]


def test_the_detector_passes_its_input_size_to_the_model(monkeypatch):
    from types import SimpleNamespace

    image = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(importlib, "import_module", lambda name: SimpleNamespace(YOLO=FakeModel))
    detector = Detector.load("yoloe-26n-mana.pt", 480)
    buf = __import__("io").BytesIO()
    image.new("RGB", (64, 48)).save(buf, "JPEG")
    assert detector.detect(buf.getvalue()) == ([], 64, 48)
    assert detector.model.weights == "yoloe-26n-mana.pt"
    assert detector.model.calls == [{"imgsz": 480, "conf": 0.3, "verbose": False}]


def test_furniture_gets_a_distance_but_a_door_does_not():
    couch = Detection("couch", 0.9, 0.0, 100.0, 200.0, 300.0)
    door = Detection("door", 0.9, 0.0, 0.0, 200.0, 480.0)
    assert [o.label for o in to_obstacles([couch, door], 640, 480)] == ["couch"]


def test_the_open_vocabulary_list_has_forty_names_and_no_repeats():
    from demo.build_detector import NAMES

    assert len(NAMES) == 40 and len(set(NAMES)) == 40


def test_build_detector_saves_the_model_told_the_names(monkeypatch, tmp_path, capsys):
    import sys
    from types import SimpleNamespace

    from demo import build_detector

    class FakeYOLOE:
        def __init__(self, base):
            self.base = base

        def get_text_pe(self, names):
            return f"pe:{len(names)}"

        def set_classes(self, names, pe):
            self.names, self.pe = names, pe

        def save(self, out):
            saved.append((self.base, self.names, self.pe, out))

    saved = []
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLOE=FakeYOLOE))
    build_detector.main(["--out", str(tmp_path / "m.pt")])
    assert saved == [("yoloe-26n-seg.pt", build_detector.NAMES, "pe:40", str(tmp_path / "m.pt"))]
    assert "40 object names" in capsys.readouterr().out
