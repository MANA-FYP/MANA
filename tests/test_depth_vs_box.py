"""tools/depth_vs_box.py (#88): reading SANPO's files and scoring the distances, on made-up data."""

import gzip
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import depth_vs_box as dvb  # noqa: E402


def test_a_depth_map_is_read_with_its_shape_from_its_first_two_values():
    depth = np.arange(6, dtype=np.float16).reshape(2, 3) + 1
    raw = gzip.compress(np.concatenate([[2, 3], depth.ravel()]).astype(np.float16).tobytes())
    assert np.array_equal(dvb.read_depth(raw), depth.astype(np.float32))


def test_objects_are_the_obstacle_classes_in_the_mask_one_per_instance():
    names = {12: "pedestrian", 1: "road"}
    mask = np.zeros((40, 60, 3), np.uint8)
    mask[..., 0] = 1  # road everywhere
    mask[5:35, 10:30] = (12, 0, 1)  # one person
    mask[5:35, 40:55] = (12, 0, 2)  # another
    found = dvb.objects(mask, names)
    assert [(o[0], o[2]) for o in found] == [
        ("pedestrian", (10, 5, 30, 35)),
        ("pedestrian", (40, 5, 55, 35)),
    ]


def test_the_true_distance_is_the_median_depth_over_the_mask_at_the_depth_maps_size():
    pixels = np.zeros((40, 60), bool)
    pixels[10:30, 10:30] = True
    depth = np.full((20, 30), 9.0, np.float32)  # half the size of the image
    depth[5:15, 5:15] = 3.0
    assert dvb.true_distance(pixels, depth) == 3.0
    assert dvb.true_distance(np.zeros((40, 60), bool), depth) is None


def test_iou():
    assert dvb.iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert dvb.iou((0, 0, 10, 10), (5, 0, 15, 10)) == 50 / 150
    assert dvb.iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_the_table_gives_the_median_error_and_the_share_within_20_percent():
    rows = [
        {"label": "person", "cut": False, "true_m": 2.5, "box_m": 2.5, "indoor_m": 3.5},
        {"label": "person", "cut": True, "true_m": 5.0, "box_m": 7.0, "indoor_m": 5.5},
        {"label": "car", "cut": False, "true_m": 10.0, "box_m": 11.0, "indoor_m": None},
    ]
    assert dvb.errors(rows, "box_m") == "10% (67%, 33%)"  # 7 m for 5 m is too far
    assert dvb.errors(rows, "indoor_m") == "25% (50%, 50%)"  # the box with no depth is left out
    text = dvb.table(dvb.with_nearer(rows), ["box", "indoor", "nearer"])
    assert "| all | 3 | 10% (67%, 33%) | 25% (50%, 50%) | 10% (100%, 0%) |" in text
    assert "| 2 to 4 m | 1 | 0% (100%, 0%) | 40% (0%, 100%) | 0% (100%, 0%) |" in text
    assert "| cut by the frame | 1 | 40% (0%, 100%) | 10% (100%, 0%) | 10% (100%, 0%) |" in text


def test_a_saved_table_is_read_back_with_numbers(tmp_path):
    path = tmp_path / "depth_vs_box.csv"
    path.write_text(
        "session,camera,frame,label,cut,true_m,box_m,indoor_m\ns,c,1,person,True,2,3,\n"
    )
    (row,) = dvb.read_rows(path)
    assert row["true_m"] == 2.0 and row["indoor_m"] is None and row["cut"] is True
