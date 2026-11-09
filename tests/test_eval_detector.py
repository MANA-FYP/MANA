"""Tests for the detector evaluation. A fake detector stands in for Ultralytics: offline."""

import json
import math

import pytest

from tools import eval_detector as ev
from tools.eval_detector import Box


def test_iou_of_same_half_and_apart():
    a = Box("x", 0, 0, 10, 10)
    assert ev.iou(a, a) == 1.0
    assert ev.iou(a, Box("x", 5, 0, 15, 10)) == pytest.approx(50 / 150)
    assert ev.iou(a, Box("x", 20, 20, 30, 30)) == 0.0


def test_zero_shot_names_map_to_sanpo_and_sanpo_names_map_to_themselves():
    sanpo = ["pedestrian", "rider", "stairs", "vehicle", "bike rack"]
    assert ev.mapping(["person", "bus", "stairs", "cup"], sanpo) == {
        "person": "pedestrian",
        "bus": "vehicle",
        "stairs": "stairs",
    }
    assert ev.mapping(["rider", "bike rack"], sanpo) == {"rider": "rider", "bike rack": "bike rack"}


def test_each_truth_box_is_found_once_highest_confidence_first():
    truth = [Box("stairs", 0, 0, 10, 10)]
    preds = [Box("stairs", 0, 0, 10, 10, 0.6), Box("stairs", 0, 0, 10, 9, 0.9)]
    kept, found = ev.match(preds, truth)
    assert kept == [("stairs", 0.9, True), ("stairs", 0.6, False)]  # the second is a duplicate
    assert found == [True]


def test_a_box_of_another_class_or_too_far_off_is_wrong():
    truth = [Box("pole", 0, 0, 10, 10)]
    kept, found = ev.match(
        [Box("stairs", 0, 0, 10, 10, 0.9), Box("pole", 6, 0, 16, 10, 0.8)], truth
    )
    assert kept == [("stairs", 0.9, False), ("pole", 0.8, False)]
    assert found == [False]


def test_a_person_on_a_rider_is_left_out_not_wrong():
    kept, _ = ev.match([Box("pedestrian", 0, 0, 10, 10, 0.9)], [Box("rider", 0, 0, 10, 10)])
    assert kept == []


def test_average_precision_perfect_none_and_half():
    assert ev.average_precision([(0.9, True), (0.8, True)], 2) == pytest.approx(1.0)
    assert ev.average_precision([(0.9, False)], 2) == 0.0
    # right, wrong, right of 2: recall 0.5 at precision 1, then 1.0 at precision 2/3
    assert ev.average_precision([(0.9, True), (0.8, False), (0.7, True)], 2) == pytest.approx(
        0.5 * 1 + 0.5 * 2 / 3
    )
    assert math.isnan(ev.average_precision([], 0))


def test_labels_in_pixels(tmp_path):
    label = tmp_path / "a.txt"
    label.write_text("1 0.5 0.5 0.2 0.4\n\n")
    assert ev.read_labels(label, ["pole", "stairs"], 100, 50) == [Box("stairs", 40, 15, 60, 35)]
    assert ev.read_labels(tmp_path / "none.txt", ["pole"], 100, 50) == []


class Fake:
    """A detector that sees in each frame what a dict says, with COCO-like names."""

    def __init__(self, sees):
        self.sees, self.imgsz = sees, 416
        self.model = type("M", (), {"names": {0: "person", 1: "stairs", 2: "cup"}})()
        self.calls = 0

    def detect_image(self, image, conf):
        from mana_perception.detection import Detection

        self.calls += 1
        return [Detection(*d) for d in self.sees.get(self.calls - 1, [])]


def dataset(tmp_path, frames):
    """A YOLO dataset of 100 x 100 test frames: frames is a list of (session, label lines)."""
    image = pytest.importorskip("PIL.Image")
    (tmp_path / "parts.json").write_text(json.dumps({"names": ["pedestrian", "stairs", "pole"]}))
    for i, (session, lines) in enumerate(frames):
        stem = f"{session}__camera_chest__{i:06d}"
        for kind in ("images", "labels"):
            (tmp_path / kind / "test").mkdir(parents=True, exist_ok=True)
        image.new("RGB", (100, 100)).save(tmp_path / "images" / "test" / f"{stem}.png")
        (tmp_path / "labels" / "test" / f"{stem}.txt").write_text("".join(f"{x}\n" for x in lines))
    return tmp_path


def test_evaluate_scores_only_named_classes_and_stairs_per_session(tmp_path):
    data = dataset(
        tmp_path,
        [
            ("s1", ["1 0.5 0.5 0.4 0.4"]),  # stairs, found
            ("s1", ["1 0.5 0.5 0.4 0.4"]),  # stairs, found only below the threshold
            ("s2", ["1 0.5 0.5 0.4 0.4", "0 0.1 0.1 0.1 0.1"]),  # stairs missed, a person
            ("s3", ["2 0.5 0.5 0.2 0.2"]),  # a pole: not named by this model
        ],
    )
    sees = {
        0: [("stairs", 0.9, 30, 30, 70, 70), ("cup", 0.9, 0, 0, 5, 5)],
        1: [("stairs", 0.1, 30, 30, 70, 70)],
        2: [("person", 0.8, 5, 5, 15, 15)],
        3: [("person", 0.5, 40, 40, 60, 60)],  # a person where there is only a pole: wrong
    }
    r = ev.evaluate(Fake(sees), data, "test", conf=0.3)
    rows = {c["class"]: c for c in r["bands"]["all"]["classes"]}
    assert set(rows) == {"pedestrian", "stairs"} and r["not_named"] == ["pole"]
    assert rows["stairs"]["objects"] == 3
    assert rows["stairs"]["recall"] == pytest.approx(1 / 3)  # only frame 0 at conf 0.3
    assert rows["stairs"]["precision"] == 1.0
    assert rows["stairs"]["ap50"] == pytest.approx(2 / 3)  # both found when the threshold is low
    assert rows["pedestrian"]["recall"] == 1.0 and rows["pedestrian"]["precision"] == 0.5
    assert r["frames"] == 4 and list(r["bands"]) == ["all"]
    assert r["bands"]["all"]["stairs"] == {
        "sessions": 2,
        "frames": 3,
        "frames_found": 1,
        "mean_recall_per_session": 0.25,  # s1 half, s2 none
        "sessions_found_at_all": 1,
    }


def test_table_reads_well(tmp_path):
    data = dataset(tmp_path, [("s1", ["1 0.5 0.5 0.4 0.4"])])
    r = ev.evaluate(Fake({0: [("stairs", 0.9, 30, 30, 70, 70)]}), data, "test", conf=0.3)
    text = ev.table("fake.pt", r)
    assert "| stairs | 1 | 1.00 | 1.00 | 1.00 |" in text
    assert "found in 1 of 1 frames" in text


def test_the_near_band_leaves_out_small_boxes_and_what_falls_on_them(tmp_path):
    data = dataset(
        tmp_path,
        [("s1", ["1 0.5 0.5 0.4 0.4", "1 0.9 0.1 0.04 0.04"])],  # stairs near, and far (4 px)
    )
    sees = {
        0: [
            ("stairs", 0.9, 30, 30, 70, 70),  # the near one, found
            ("stairs", 0.8, 88, 8, 92, 12),  # the far one, found
            ("stairs", 0.7, 0, 90, 5, 95),  # a small wrong box
        ]
    }
    r = ev.evaluate(Fake(sees), data, "test", conf=0.3, min_height=0.1)
    near = next(b for name, b in r["bands"].items() if name.startswith("near"))
    every = {c["class"]: c for c in r["bands"]["all"]["classes"]}["stairs"]
    close = {c["class"]: c for c in near["classes"]}["stairs"]
    assert every["objects"] == 2 and every["precision"] == pytest.approx(2 / 3)
    assert close["objects"] == 1 and close["precision"] == 1.0 and close["recall"] == 1.0
    assert "Objects: near (box at least 10% of the frame's height)" in ev.table("x.pt", r)
