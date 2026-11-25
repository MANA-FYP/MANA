"""mana_perception.glass without a model: which glass counts as in the path, where it meets the
floor, and that a flicker is not enough."""

import math

import numpy as np
import pytest

from mana_perception import glass

S = glass.SIZE


def mask(rows, cols, p=0.9):
    m = np.zeros((S, S), np.float32)
    m[rows, cols] = p
    return m


def test_glass_in_the_middle_third_is_in_the_path_with_its_bottom_and_centre():
    ahead = glass.in_path(mask(slice(40, 240), slice(120, 200)))
    assert ahead is not None and ahead.confidence == pytest.approx(0.9)
    assert ahead.bottom == pytest.approx(240 / S, abs=0.01)
    assert ahead.centre == pytest.approx(0.5, abs=0.01)


def test_glass_off_to_the_side_or_too_little_of_it_is_not():
    assert glass.in_path(mask(slice(0, S), slice(0, 100))) is None  # the left third
    assert glass.in_path(mask(slice(0, 10), slice(120, 140))) is None  # under 2 % of the middle
    assert glass.in_path(mask(slice(0, S), slice(107, 213), p=0.4)) is None  # not over 0.5


def test_the_floor_row_gives_the_distance_from_height_and_tilt():
    fy, cy, h, pitch = 500.0, 240.0, 1.0, 0.3
    # the image centre looks 0.3 rad down from 1 m: the floor 1 / tan(0.3) ahead
    assert glass.floor_distance(cy, fy, cy, h, pitch) == pytest.approx(1 / math.tan(0.3))
    lower = glass.floor_distance(400, fy, cy, h, pitch)
    assert lower < 1 / math.tan(0.3)  # lower in the picture is nearer
    assert glass.floor_distance(0, fy, cy, h, 0.0) is None  # above the horizon


def test_glass_counts_only_after_frames_in_a_row():
    r = glass.InARow()
    assert [r.see(x) for x in (True, True, False, True, True, True)] == [
        False, False, False, False, False, True,
    ]  # fmt: skip


def test_no_model_without_weights():
    assert glass.load("no/such/weights.pt") is None
