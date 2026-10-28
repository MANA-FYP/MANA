"""Tests for the 3D camera ground reader (mana_perception.depth). Pure numpy, no ROS.

The depth frames are rendered by mana_perception.synthetic, which casts a ray for every pixel at a
ground shape: flat ground, ramps, a curb, stairs up and down, drops and a wall. The camera is the
3D camera planned for the chair: 1.0 m up, tilted 0.6 rad down, 87 degrees across, with a blind
zone to about 0.37 m. When the Gazebo world (#13, #14) exists, frames saved from it go through the
same checks.
"""

import math
import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "mana_perception"))

from mana_perception.synthetic import flat, ramp, render, stairs, step  # noqa: E402

from mana_perception import depth as dp  # noqa: E402
from mana_perception import synthetic as syn  # noqa: E402

COLS, ROWS, CAM = syn.COLS, syn.ROWS, syn.CAMERA


def read(ground, **kw):
    return dp.read_ground(render(ground, **kw), kw.get("cam", CAM))


def test_codes_match_the_message_file():
    text = (ROOT / "src" / "mana_msgs" / "msg" / "GroundReport.msg").read_text()
    codes = {n: int(v) for n, v in re.findall(r"^uint8 (\w+)=(\d+)", text, re.M)}
    for name in ("DEPTH_CAMERA", "FLAT", "RAMP", "STAIRS_UP", "STAIRS_DOWN", "CURB", "DROP"):
        assert getattr(dp, name) == codes[name]
    assert dp.UNKNOWN == codes["UNKNOWN"]


def test_flat_ground_is_flat_with_no_distance():
    r = read(flat)
    assert r.ground == dp.FLAT
    assert math.isnan(r.distance_m) and math.isnan(r.step_height_m)


def test_a_ramp_up_or_down_is_a_ramp_where_it_starts():
    for slope in (0.08, -0.08):
        r = read(ramp(1.5, slope))
        assert r.ground == dp.RAMP, slope
        assert r.distance_m == pytest.approx(1.5, abs=0.15)


def test_a_steep_slope_is_still_a_ramp_not_flat_ground():
    assert read(ramp(1.2, 0.3)).ground == dp.RAMP


def test_one_low_step_up_is_a_curb_with_its_height():
    r = read(step(1.0, 0.12))
    assert r.ground == dp.CURB
    assert r.distance_m == pytest.approx(1.0, abs=0.1)
    assert r.step_height_m == pytest.approx(0.12, abs=0.03)


def test_stairs_up_are_stairs_up_at_the_first_riser():
    r = read(stairs(1.5, 0.17))
    assert r.ground == dp.STAIRS_UP
    assert r.distance_m == pytest.approx(1.5, abs=0.1)
    assert r.step_height_m == pytest.approx(0.17, abs=0.04)


def test_stairs_down_seen_from_above_are_stairs_down_at_the_edge():
    r = read(stairs(1.0, -0.17))
    assert r.ground == dp.STAIRS_DOWN
    assert r.distance_m == pytest.approx(1.0, abs=0.1)
    assert r.step_height_m == pytest.approx(0.17, abs=0.05)


def test_a_drop_with_the_floor_in_sight_gives_its_edge_and_height():
    r = read(step(0.8, -0.3))
    assert r.ground == dp.DROP
    assert r.distance_m == pytest.approx(0.8, abs=0.1)
    assert r.step_height_m == pytest.approx(0.3, abs=0.05)


def test_a_drop_with_the_floor_out_of_sight_is_a_drop_of_unknown_height():
    r = read(step(1.0, -3.0))  # a ledge: the floor below is beyond the sensor's range
    assert r.ground == dp.DROP
    assert r.distance_m == pytest.approx(1.0, abs=0.1)
    assert math.isnan(r.step_height_m)


def test_a_drop_close_ahead_is_reported_close_enough_for_the_core_to_stop():
    r = read(step(0.6, -0.3))
    assert r.ground == dp.DROP and r.distance_m <= 0.7  # the core stops at 0.5 m


@pytest.mark.parametrize("at", [1.2, 1.5, 2.0])
def test_a_drop_farther_away_hides_floor_behind_its_edge_and_is_still_found(at):
    r = read(step(at, -0.25))
    assert r.ground == dp.DROP
    assert r.distance_m == pytest.approx(at, abs=0.1)


@pytest.mark.parametrize("at", [1.03, 1.5, 2.0])
def test_stairs_and_curbs_are_found_wherever_the_riser_falls_between_bins(at):
    assert read(stairs(at, 0.17)).ground == dp.STAIRS_UP
    r = read(step(at, 0.12))
    assert r.ground == dp.CURB
    assert r.step_height_m == pytest.approx(0.12, abs=0.03)


def test_a_low_step_down_is_a_curb_not_a_drop():
    assert read(step(1.0, -0.12)).ground == dp.CURB


def test_a_wall_is_not_read_as_stairs():
    r = read(step(2.0, 2.5))
    assert r.ground == dp.FLAT


def test_noise_and_missing_pixels_do_not_change_the_reading():
    rng = np.random.default_rng(7)
    for ground, expected in ((flat, dp.FLAT), (stairs(1.5, 0.17), dp.STAIRS_UP)):
        image = syn.noisy(render(ground), rng)  # 1 % depth noise, 10 % of pixels missing
        image[rng.random(image.shape) < 0.02] = 0.0
        assert dp.read_ground(image, CAM).ground == expected


def test_a_two_degree_tilt_error_on_flat_ground_is_not_a_ramp():
    image = render(flat)
    tilted = dp.Camera(CAM.fx, CAM.fy, CAM.cx, CAM.cy, CAM.height_m, CAM.pitch_rad + 0.035)
    assert dp.read_ground(image, tilted).ground == dp.FLAT


def test_a_mounting_height_error_of_a_few_centimetres_does_not_matter():
    for height in (0.97, 1.03):
        cam = dp.Camera(CAM.fx, CAM.fy, CAM.cx, CAM.cy, height, CAM.pitch_rad)
        assert dp.read_ground(render(stairs(1.5, 0.17)), cam).ground == dp.STAIRS_UP
        assert dp.read_ground(render(flat), cam).ground == dp.FLAT


def test_the_blind_zone_ends_where_the_bottom_row_meets_the_ground():
    assert dp.nearest_ground(CAM, ROWS) == pytest.approx(0.37, abs=0.01)
    up = dp.Camera.from_fov(COLS, ROWS, math.radians(87.0), height_m=1.0, pitch_rad=-0.7)
    assert dp.nearest_ground(up, ROWS) == math.inf


@pytest.mark.parametrize("at", [0.1, 0.2, 0.3, 0.4, 0.45])
def test_an_edge_down_in_the_blind_zone_is_reported_no_farther_than_it_is(at):
    for shape, kinds in (
        (stairs(at, -0.17), (dp.STAIRS_DOWN,)),
        (step(at, -0.3), (dp.DROP,)),
        (step(at, -3.0), (dp.DROP,)),  # a ledge: the floor below is out of range
    ):
        r = dp.read_ground(render(shape), CAM)
        assert r.ground in kinds, (at, r)
        assert r.distance_m <= at, (at, r)  # never farther than the real edge


@pytest.mark.parametrize("at", [0.1, 0.3, 0.45])
def test_a_curb_up_in_the_blind_zone_is_a_curb_right_in_front(at):
    r = read(step(at, 0.12))
    assert r.ground == dp.CURB and r.distance_m <= at
    assert r.step_height_m == pytest.approx(0.12, abs=0.03)


def test_the_drop_in_the_blind_zone_gives_its_measured_height():
    r = read(step(0.3, -0.3))
    assert r.step_height_m == pytest.approx(0.3, abs=0.04)


def test_a_wall_right_in_front_is_left_to_the_obstacle_sensors():
    assert read(step(0.3, 2.5)).ground == dp.UNKNOWN


def test_stairs_up_starting_in_the_blind_zone_are_stairs_not_a_wall():
    shallow = dp.Camera.from_fov(COLS, ROWS, math.radians(87.0), height_m=1.0, pitch_rad=0.5)
    r = dp.read_ground(render(stairs(0.1, 0.17), cam=shallow), shallow)
    assert r.ground == dp.STAIRS_UP and r.distance_m == 0.0


def test_a_floor_seen_only_beyond_the_reading_range_is_a_ledge_and_no_depth_is_unknown():
    # the floor 8 m down shows only past max_range_m (3 m): a ledge right in front
    deep = dp.read_ground(render(step(0.2, -8.0), far=20.0), CAM)
    assert deep.ground == dp.DROP and deep.distance_m == 0.0
    # no depth at all, as from a covered lens or a floor beyond the sensor itself: nothing to judge
    assert dp.read_ground(np.full((ROWS, COLS), np.nan), CAM).ground == dp.UNKNOWN


def test_no_readings_at_all_is_unknown():
    assert dp.read_ground(np.full((ROWS, COLS), np.nan), CAM).ground == dp.UNKNOWN


def test_depth_images_in_metres_or_millimetres_become_metres():
    metres = np.array([[1.5, np.nan], [2.0, 0.5]], dtype="<f4")
    out = dp.to_metres("32FC1", 2, 2, 8, metres.tobytes())
    assert out[0, 0] == pytest.approx(1.5) and math.isnan(out[0, 1])
    mm = np.array([[1500, 0, 7], [2000, 500, 7]], dtype=">u2")  # padded rows, big-endian
    out = dp.to_metres("16UC1", 2, 2, 6, mm.tobytes(), big_endian=True)
    assert out.shape == (2, 2)
    assert out[1, 0] == pytest.approx(2.0) and math.isnan(out[0, 1])
    assert dp.to_metres("rgb8", 2, 2, 6, b"\0" * 12) is None


def test_the_profile_bins_heights_along_the_path():
    prof = dp.profile(render(step(1.0, 0.12)), CAM)
    have = ~np.isnan(prof.height_m)
    d, h = prof.distance_m[have], prof.height_m[have]
    assert np.all(np.abs(h[d < 0.9]) < 0.02)
    assert np.all(np.abs(h[d > 1.1] - 0.12) < 0.02)
    assert prof.visible_until_m == pytest.approx(3.0)


def test_how_far_the_camera_sees_the_ground_depends_on_its_tilt():
    level = dp.Camera.from_fov(COLS, ROWS, math.radians(87.0), height_m=1.0, pitch_rad=0.0)
    assert dp.visible_until(level, ROWS, 3.0) == 3.0  # the top row looks above the horizon
    steep = dp.Camera.from_fov(COLS, ROWS, math.radians(87.0), height_m=1.0, pitch_rad=1.2)
    assert dp.visible_until(steep, ROWS, 3.0) < 3.0


def test_the_chairs_own_pitch_is_added_to_the_cameras_tilt():
    on_slope = dp.tilted(CAM, 0.2)
    assert on_slope.pitch_rad == pytest.approx(CAM.pitch_rad + 0.2)
    assert on_slope.height_m == pytest.approx(CAM.height_m / math.cos(0.2))  # over the ground below
    assert dp.tilted(CAM, 0.0) == CAM


@pytest.mark.parametrize("chair_pitch", [0.2, -0.2])
def test_a_pitched_chair_on_level_ground_reads_no_hazard_only_with_its_pitch(chair_pitch):
    # nose down where a ramp meets level ground, or nose up: the mounting tilt alone reads a step
    # (nose up, CURB at 0 m) or a slope that is not there (nose down, RAMP at 0.5 m).
    # With the pitch it is RAMP at 0 m: against the plane the chair stands on, the ground ahead
    # does change slope there, and the core only slows down.
    image = render(flat, cam=dp.tilted(CAM, chair_pitch))
    assert dp.read_ground(image, CAM).ground != dp.FLAT
    assert dp.read_ground(image, CAM, chair_pitch_rad=chair_pitch).ground in (dp.FLAT, dp.RAMP)


def test_on_the_way_down_the_stairs_the_steps_below_are_stairs_down():
    slope = math.atan(0.17 / 0.33)  # the simulation's stairs (src/mana_sim, #74)
    image = render(stairs(0.0, -0.17, run=0.33), cam=dp.tilted(CAM, slope))
    assert dp.read_ground(image, CAM, chair_pitch_rad=slope).ground == dp.STAIRS_DOWN


# the simulation's 3D camera (src/mana_sim, #92): 1.2 rad across, 320 x 240, 0.97 m up, 0.6 rad down
SIM_CAM = dp.Camera.from_fov(320, 240, 1.2, 0.97, 0.6)


def with_noise(ground, sigma_m, seed):
    """A frame from the simulation's camera with Gaussian noise of sigma_m on every pixel, as the
    simulation's depth_noise fault adds it (src/mana_sim/mana_sim/faults.py)."""
    image = render(ground, cam=SIM_CAM, rows=240, cols=320)
    rng = np.random.default_rng(seed)
    return np.where(np.isfinite(image), image + rng.normal(0.0, sigma_m, image.shape), image)


@pytest.mark.parametrize("seed", range(5))
def test_depth_noise_of_5_cm_does_not_make_a_flat_floor_a_step(seed):
    # before rows were taken as medians, this read as a CURB in front every time
    assert dp.read_ground(with_noise(flat, 0.05, seed), SIM_CAM).ground == dp.FLAT


@pytest.mark.parametrize("seed", range(3))
def test_depth_noise_of_5_cm_keeps_every_hazard_and_where_it_is(seed):
    cases = [
        (stairs(1.0, 0.17), dp.STAIRS_UP, 1.0),
        (step(1.0, 0.12), dp.CURB, 1.0),
        (stairs(0.9, -0.17), dp.STAIRS_DOWN, 0.9),
        (step(0.8, -1.0), dp.DROP, 0.8),  # read as a CURB before
    ]
    for ground, kind, at in cases:
        r = dp.read_ground(with_noise(ground, 0.05, seed), SIM_CAM)
        assert r.ground == kind
        assert at - 0.15 <= r.distance_m <= at + 0.1
    blind = dp.read_ground(with_noise(step(0.3, -1.0), 0.05, seed), SIM_CAM)
    assert blind.ground == dp.DROP and blind.distance_m == 0.0


def test_a_row_with_too_few_readings_is_left_out():
    image = render(flat, cam=SIM_CAM, rows=240, cols=320)
    image[200:, :] = np.nan
    empty = dp.ground_points(image, SIM_CAM)
    image[200:, 160] = 9.0  # one stray reading in each of the bottom rows: too few to count
    stray = dp.ground_points(image, SIM_CAM)
    assert all(np.array_equal(a, b) for a, b in zip(empty, stray, strict=True))


RAMP_RAD = 0.1244  # the ramps of the simulation's route (mana_route.sdf)


def on_slope(pitch, extra=None):
    """The ground seen from a chair standing on a slope that goes on ahead (nose down positive),
    with something else on it from extra(ahead)."""
    return lambda a: -a * math.tan(pitch) + (extra(a) if extra else 0.0)


@pytest.mark.parametrize("pitch", [-RAMP_RAD, RAMP_RAD], ids=["going-up", "going-down"])
def test_on_the_routes_ramp_the_ramp_itself_is_not_a_step(pitch):
    # with the pitch added, the nearest ground in view is about 7 cm off the level plane, which
    # read as a curb at 0 m before the blind-zone check allowed for the slope
    rng = np.random.default_rng(1)
    for noise in (0.0, 0.01):
        image = render(on_slope(pitch), cam=dp.tilted(SIM_CAM, pitch), rows=240, cols=320)
        if noise:
            image = image * (1.0 + rng.normal(0.0, noise, image.shape))
        r = dp.read_ground(image, SIM_CAM, chair_pitch_rad=pitch)
        assert r.ground in (dp.FLAT, dp.RAMP), r


@pytest.mark.parametrize("pitch", [-RAMP_RAD, RAMP_RAD], ids=["going-up", "going-down"])
def test_a_curb_or_a_drop_on_the_ramp_is_still_found_where_it_is(pitch):
    cam = dp.tilted(SIM_CAM, pitch)
    for extra, kind in ((step(1.0, 0.1), dp.CURB), (step(1.0, -0.3), dp.DROP)):
        image = render(on_slope(pitch, extra), cam=cam, rows=240, cols=320)
        r = dp.read_ground(image, SIM_CAM, chair_pitch_rad=pitch)
        assert r.ground == kind and r.distance_m == pytest.approx(1.0, abs=0.1)


@pytest.mark.parametrize("pitch", [-RAMP_RAD, RAMP_RAD], ids=["going-up", "going-down"])
@pytest.mark.parametrize("height", [0.1, -0.1, 0.2, -0.2], ids=lambda h: f"{h:+.1f}m")
def test_an_edge_in_the_blind_zone_on_the_ramp_is_still_an_edge_at_0_m(pitch, height):
    # 0.3 m ahead is in the blind zone (the nearest ground in view is about 0.53 m ahead). From
    # #100's review: a drop going up, or a raised edge going down, brings the ground in view back
    # towards level, so it is measured from the plane the chair stands on, not from level.
    cam = dp.tilted(SIM_CAM, pitch)
    image = render(on_slope(pitch, step(0.3, height)), cam=cam, rows=240, cols=320)
    r = dp.read_ground(image, SIM_CAM, chair_pitch_rad=pitch)
    assert r.ground in (dp.CURB, dp.DROP) and r.distance_m == 0.0, r


@pytest.mark.parametrize("start", [0.0, 0.2, 0.35])
@pytest.mark.parametrize("slope", [math.tan(RAMP_RAD), -math.tan(RAMP_RAD)], ids=["foot", "crest"])
@pytest.mark.parametrize("height_error", [-0.015, 0.015])
def test_a_ramp_that_starts_in_the_blind_zone_is_a_ramp_not_an_edge(start, slope, height_error):
    # at the foot or the crest of the route's ramp the chair is still level while the nearest
    # ground in view is already 6 to 9 cm up or down the slope: a false curb at 0 m on #100's
    # Gazebo run. The camera is really height_error off the height depth_node is told.
    seen_from = dp.Camera.from_fov(320, 240, 1.2, SIM_CAM.height_m + height_error, 0.6)
    rng = np.random.default_rng(2)
    image = render(ramp(start, slope), cam=seen_from, rows=240, cols=320)
    for frame in (image, syn.noisy(image, rng)):
        r = dp.read_ground(frame, SIM_CAM)
        assert r.ground == dp.RAMP, r
        assert r.distance_m <= max(start, dp.nearest_ground(SIM_CAM, 240)) + 0.1


@pytest.mark.parametrize("height_error", [-0.015, 0.015])
@pytest.mark.parametrize(
    "at, height, slope",
    [(0.3, 0.1, 0.125), (0.2, 0.08, 0.125), (0.3, -0.1, -0.125), (0.3, -0.3, -0.125),
     (0.3, -0.3, 0.125), (0.15, -0.2, 0.125), (0.3, 0.15, -0.125)],
    ids=["curb-then-ramp-up", "low-curb-then-ramp-up", "step-down-then-ramp-down",
         "drop-then-ramp-down", "drop-then-ramp-up", "near-drop-then-ramp-up",
         "curb-then-ramp-down"],
)  # fmt: skip
def test_an_edge_in_the_blind_zone_with_a_ramp_behind_it_is_still_an_edge(
    at, height, slope, height_error
):
    # the slope behind the edge meets the chair's level on the near side, not in the blind zone,
    # or, with the slope the other way, only past the ground in view
    seen_from = dp.Camera.from_fov(320, 240, 1.2, SIM_CAM.height_m + height_error, 0.6)
    edge = lambda a: np.where(a < at, 0.0, height + slope * np.clip(a - at, 0.0, None))  # noqa: E731
    r = dp.read_ground(render(edge, cam=seen_from, rows=240, cols=320), SIM_CAM)
    assert r.ground in (dp.CURB, dp.DROP) and r.distance_m == 0.0, r


def test_a_slope_too_steep_for_a_ramp_from_the_blind_zone_is_still_an_edge():
    r = dp.read_ground(render(ramp(0.3, 0.3), cam=SIM_CAM, rows=240, cols=320), SIM_CAM)
    assert r.ground == dp.CURB and r.distance_m == 0.0


def render_across(ground, cam, rows=120, cols=160, far=6.0):
    """Like synthetic.render, but the ground may change across the path too: ground(ahead, left)
    is the height there. Each pixel's ray is marched on its own, so it is slower; a small image."""
    z = np.arange(0.05, far, 0.005)
    c, s = math.cos(cam.pitch_rad), math.sin(cam.pitch_rad)
    image = np.full((rows, cols), np.inf)
    for v in range(rows):
        yn = (v - cam.cy) / cam.fy
        ahead = z * (c - yn * s)
        height = cam.height_m - z * (yn * c + s)
        for u in range(cols):
            left = -z * (u - cam.cx) / cam.fx
            hit = np.nonzero(height <= ground(ahead, left))[0]
            if hit.size:
                image[v, u] = z[hit[0]]
    return image


def test_a_drop_whose_edge_runs_at_an_angle_across_the_path_is_still_a_fall():
    # the row median keeps what covers most of a row; an edge 30 degrees across the path still
    # gives a drop (or stairs down, which the core stops for the same way) at or before its centre
    cam = dp.Camera.from_fov(160, 120, 1.2, 0.97, 0.6)
    edge = math.tan(math.radians(30))
    image = render_across(lambda a, y: np.where(a + y * edge >= 1.0, -1.0, 0.0), cam)
    r = dp.read_ground(image, cam)
    assert r.ground in (dp.DROP, dp.STAIRS_DOWN)
    assert r.distance_m <= 1.15
