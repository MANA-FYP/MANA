"""Glass in the path (demo.vision.GlassSegmenter, demo.server.glass_ahead): only glass in the
middle third counts, and its distance is where its bottom edge meets the floor."""

import io
import math

import numpy as np
import pytest
from PIL import Image

from demo import server
from demo.vision import GlassSegmenter

S = 320  # the segmenter's mask size


class Fixed(GlassSegmenter):
    """A segmenter whose mask is set by the test."""

    def __init__(self, mask):
        super().__init__([], "cpu")
        self.fixed = mask

    def segment(self, jpeg, depth=None):
        self.mask = self.fixed
        return self.mask


def mask(rows, cols):
    m = np.zeros((S, S), np.float32)
    m[rows, cols] = 0.9
    return m


def test_glass_in_the_middle_of_the_picture_is_in_the_path():
    seg = Fixed(mask(slice(40, 240), slice(110, 210)))
    seg.segment(b"")
    box = seg.in_path(640, 480)
    assert box is not None and box.label == "glass"
    assert 200 < box.x1 < box.x2 < 440  # within the middle third of 640
    bottom = np.percentile(np.arange(40, 240), 98)  # the glass's lowest rows, in mask pixels
    assert box.y2 == pytest.approx((bottom + 1) * 480 / S)


def test_glass_off_to_the_side_is_not_in_the_path():
    seg = Fixed(mask(slice(40, 240), slice(0, 90)))  # a window on the left wall
    seg.segment(b"")
    assert seg.in_path(640, 480) is None


def jpeg():
    out = io.BytesIO()
    Image.new("RGB", (640, 480), "white").save(out, "JPEG")
    return out.getvalue()


def tilted(pitch_deg):
    g, p = 9.81, math.radians(pitch_deg)
    return (0.0, g * math.cos(p), g * math.sin(p))


def test_glass_reaching_the_floor_ahead_is_an_obstacle_where_it_meets_it():
    app = server.build_app(glass=Fixed(mask(slice(40, 240), slice(110, 210))))
    assert server.glass_ahead(app, jpeg(), 640, 480) is None  # no tilt yet: no distance
    app["mw"].gravity = tilted(20)
    ob = server.glass_ahead(app, jpeg(), 640, 480)
    assert ob.label == "glass" and 1.0 < ob.distance_m < 6.0
    assert abs(ob.bearing_rad) < 0.05  # straight ahead


def test_glass_whose_bottom_is_above_the_horizon_is_not_on_the_floor_ahead():
    app = server.build_app(glass=Fixed(mask(slice(0, 60), slice(110, 210))))
    app["mw"].gravity = tilted(0)  # held level: the top of the picture is above the horizon
    assert server.glass_ahead(app, jpeg(), 640, 480) is None
