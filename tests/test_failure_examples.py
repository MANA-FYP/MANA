"""Tests for the failure pictures (#51). A fake detector stands in for Ultralytics: offline."""

import sys

import pytest

from tests.test_eval_detector import Fake, dataset
from tools import failure_examples as fe

Box = fe.Box
match = sys.modules[Box.__module__].match  # eval_detector's, as failure_examples imports it


def test_judge_scores_as_match_does():
    truth = [Box("stairs", 0, 0, 10, 10), Box("rider", 50, 50, 60, 60), Box("stairs", 80, 0, 90, 3)]
    preds = [
        Box("stairs", 0, 0, 10, 10, 0.6),  # a duplicate of the next: wrong
        Box("stairs", 0, 0, 10, 9, 0.9),  # right
        Box("pedestrian", 50, 50, 60, 60, 0.8),  # on a rider: left out
        Box("stairs", 30, 30, 40, 40, 0.5),  # nothing there: wrong
        Box("stairs", 80, 0, 90, 3, 0.7),  # on a box too small for the band: left out
    ]
    ignore, near = truth[2:], truth[:2]
    judged, found = fe.judge(preds, near, ignore, min_px=5)
    scored, found_by_match = match(preds, near, ignore, min_px=5)
    assert [(p.cls, p.conf, right) for p, right in judged] == scored
    assert found == found_by_match
    assert [p.conf for p, right in judged if not right] == [0.6, 0.5]


def test_a_near_box_missed_is_listed_and_a_far_one_is_not():
    truth = [Box("stairs", 0, 0, 40, 40), Box("stairs", 60, 60, 64, 64)]  # near, and 4 px high
    missed, false = fe.failures([], truth, "stairs", conf=0.3, min_px=10)
    assert missed == [truth[0]] and false == []


def test_false_boxes_count_only_at_the_kept_confidence():
    truth = [Box("stairs", 0, 0, 40, 40)]
    preds = [
        Box("stairs", 0, 0, 40, 40, 0.9),  # found it
        Box("stairs", 50, 50, 90, 90, 0.4),  # nothing there: false
        Box("stairs", 50, 0, 90, 40, 0.2),  # under the node's 0.3: not counted
        Box("pole", 50, 50, 90, 90, 0.9),  # another class: not this class's failure
    ]
    missed, false = fe.failures(preds, truth, "stairs", conf=0.3, min_px=10)
    assert missed == [] and [p.conf for p in false] == [0.4]


def test_pick_takes_the_strongest_one_per_walk():
    found = [
        {"walk": "a", "strength": 0.2},
        {"walk": "a", "strength": 0.9},
        {"walk": "b", "strength": 0.5},
        {"walk": "c", "strength": 0.1},
    ]
    assert [(f["walk"], f["strength"]) for f in fe.pick(found, 2)] == [("a", 0.9), ("b", 0.5)]


def test_collect_and_draw(tmp_path):
    pytest.importorskip("PIL.Image")
    data = dataset(
        tmp_path,
        [
            ("s1", ["1 0.5 0.5 0.4 0.4"]),  # stairs, missed
            ("s2", ["0 0.5 0.5 0.2 0.4"]),  # a person, and stairs seen where there are none
        ],
    )
    sees = {0: [], 1: [("stairs", 0.8, 10, 10, 40, 40)]}
    found = fe.collect(Fake(sees), data, "test", ["stairs", "pole"], conf=0.3, min_height=0.1)
    kinds = sorted((f["walk"], f["class"], f["kind"]) for f in found)
    assert kinds == [("s1", "stairs", "missed"), ("s2", "stairs", "false")]
    missed = next(f for f in found if f["kind"] == "missed")
    assert missed["box"] == [30.0, 30.0, 70.0, 70.0] and missed["strength"] == pytest.approx(0.4)

    from PIL import Image

    truth = [Box("stairs", 30, 30, 70, 70)]
    picture = fe.draw(Image.new("RGB", (100, 100)), missed, [], truth, width=200)
    assert picture.size == (200, 200)
    assert picture.getpixel((60, 100)) == fe.GREEN  # the missed box's left edge, scaled
