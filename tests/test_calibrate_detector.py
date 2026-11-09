"""tools/calibrate_detector.py (#87): judging detections against masks, and the calibration."""

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "src" / "mana_perception")]

import calibrate_detector as cd  # noqa: E402
from mana_perception.detection import Detection  # noqa: E402


def test_a_detection_is_right_when_it_meets_an_object_of_its_kind_once():
    found = [("pedestrian", (100, 100, 150, 250))]
    dets = [
        Detection("person", 0.9, 100, 100, 150, 250),  # right
        Detection("person", 0.6, 102, 100, 150, 250),  # the same person again: wrong
        Detection("car", 0.8, 100, 100, 150, 250),  # not a person's kind: wrong
        Detection("person", 0.7, 400, 100, 450, 250),  # nothing there: wrong
        Detection("person", 0.95, 0, 0, 10, 20),  # too small to judge: left out
        Detection("umbrella", 0.9, 100, 100, 150, 250),  # not an obstacle class: left out
    ]
    assert cd.judge(dets, found) == [
        ("person", 0.9, True),
        ("car", 0.8, False),
        ("person", 0.7, False),
        ("person", 0.6, False),
    ]


def test_objects_are_the_classes_that_can_be_obstacles():
    mask = np.zeros((60, 80, 3), np.uint8)
    mask[10:50, 10:30] = (12, 0, 1)  # a pedestrian, 800 pixels
    mask[10:50, 40:60] = (3, 0, 0)  # a sidewalk
    names = {12: "pedestrian", 3: "sidewalk"}
    assert cd.objects(mask, names) == [("pedestrian", (10, 10, 30, 50))]


def test_reliability_bins_and_the_calibration_error():
    conf = np.array([0.15, 0.15, 0.95, 0.95])
    right = np.array([False, False, True, False])
    rows, ece = cd.reliability(conf, right)
    assert [(n, round(c, 2), r) for _, _, n, c, r in rows] == [(2, 0.15, 0.0), (2, 0.95, 0.5)]
    assert ece == pytest.approx(0.5 * 0.15 + 0.5 * 0.45)


def test_platt_scaling_recovers_a_known_map():
    rng = np.random.default_rng(0)
    conf = rng.uniform(0.05, 0.95, 20000)
    truth = cd.trust(conf, 2.0, -1.0)  # an overconfident detector
    right = rng.uniform(size=conf.size) < truth
    a, b = cd.fit_platt(conf, right)
    assert a == pytest.approx(2.0, abs=0.1) and b == pytest.approx(-1.0, abs=0.1)
    calibrated = cd.trust(conf, a, b)
    assert cd.reliability(calibrated, right)[1] < cd.reliability(conf, right)[1]


def test_isotonic_regression_is_monotone_and_follows_the_data():
    conf = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    right = np.array([0, 1, 0, 1, 1, 1], dtype=bool)
    x, y = cd.fit_isotonic(conf, right)
    assert np.all(np.diff(y) >= 0)
    assert y[0] == 0.0 and y[-1] == 1.0
    assert cd.trust_isotonic([0.0, 1.0], (x, y)).tolist() == [0.0, 1.0]  # held at the ends
