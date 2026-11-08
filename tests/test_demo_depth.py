"""Depth from the camera image (#131): the scale set from the floor, the ground read from it, the
nearest thing in the path, and box distances. The depth images are rendered by
mana_perception.synthetic, then spoiled the way a monocular model spoils them: the scale off by up
to 20 percent, a gradual warp and noise. No model is needed: a stand-in returns the rendered image.
"""

import asyncio
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "mana_perception"))

from mana_perception.depth import CURB, DROP, FLAT, STAIRS_UP  # noqa: E402

from demo import depth as dz  # noqa: E402
from mana_perception import synthetic as syn  # noqa: E402

COLS, ROWS = 350, 266  # the model's output for a 4:3 frame at size 266
MOUNT = dz.Mount(height_m=1.2, long_fov_deg=66.0)
PITCH = math.radians(30)


def scene(ground, pitch=PITCH, scale=1.0, warp=0.0, noise=0.0, seed=0):
    cam = MOUNT.camera(COLS, ROWS, pitch)
    img = syn.render(ground, cam, ROWS, COLS)
    rng = np.random.default_rng(seed)
    rows = np.linspace(-1, 1, ROWS)[:, None]
    img = img * scale * (1 + warp * rows) * (1 + rng.normal(0, noise, img.shape))
    return np.where(np.isfinite(img), img, 20.0)  # a model always answers: far, not missing


def wall(at):
    return lambda x: np.where(x >= at, 3.0, 0.0)


# ---- the camera's tilt from the phone


@pytest.mark.parametrize(
    "gravity, degrees",
    [((0, 9.81, 0), 0), ((0, 0, 9.81), 90), ((0, 8.5, 4.9), 30), ((0, 9.81, -1.7), -10)],
)
def test_the_camera_tilt_from_gravity(gravity, degrees):
    assert math.degrees(dz.camera_pitch(*gravity)) == pytest.approx(degrees, abs=0.5)


def test_no_tilt_from_a_phone_in_free_fall():
    assert dz.camera_pitch(0.1, 0.2, 0.3) is None


# ---- the scale from the floor


@pytest.mark.parametrize("scale", [0.8, 1.0, 1.2])
def test_the_floor_sets_the_scale(scale):
    cam = MOUNT.camera(COLS, ROWS, PITCH)
    found = dz.floor_scale(scene(syn.flat, scale=scale, noise=0.02), cam)
    assert found * scale == pytest.approx(1.0, abs=0.02)


def test_no_scale_when_the_camera_looks_too_level_to_see_the_floor():
    cam = MOUNT.camera(COLS, ROWS, math.radians(5))
    assert dz.floor_scale(scene(syn.flat, pitch=math.radians(5)), cam) is None
    seen = dz.read(scene(syn.flat, pitch=math.radians(5)), MOUNT, math.radians(5))
    assert seen.reading is None and seen.why == "tilt the camera down to the floor"


def test_no_scale_when_the_bottom_of_the_picture_is_not_a_floor():
    cam = MOUNT.camera(COLS, ROWS, PITCH)
    rng = np.random.default_rng(1)
    clutter = rng.uniform(0.5, 4.0, (ROWS, COLS))  # a cluttered table, not a plane
    assert dz.floor_scale(clutter, cam) is None


# ---- the ground, with the model's scale off


ERRORS = [
    {"scale": 0.8},
    {"scale": 1.2},
    {"warp": 0.05},
    {"warp": -0.05},
    {"scale": 1.2, "warp": 0.05, "noise": 0.05},
]


@pytest.mark.parametrize("error", ERRORS)
@pytest.mark.parametrize("pitch_deg", [20, 30, 45])
@pytest.mark.parametrize(
    "ground, kind, at",
    [
        (syn.flat, FLAT, None),
        (syn.step(1.8, 0.12), CURB, 1.8),
        (syn.stairs(2.0, 0.17), STAIRS_UP, 2.0),
        (syn.stairs(2.0, -0.17), DROP, 2.0),  # the lower steps are hidden: a drop at the edge
        (syn.step(2.0, -1.0), DROP, 2.0),
    ],
)
def test_the_ground_is_read_through_the_models_errors(ground, kind, at, pitch_deg, error):
    pitch = math.radians(pitch_deg)
    seen = dz.read(scene(ground, pitch=pitch, **error), MOUNT, pitch)
    assert seen.reading.ground == kind
    if at is None:
        assert math.isnan(seen.reading.distance_m)
    else:
        assert seen.reading.distance_m == pytest.approx(at, abs=0.25)


def test_without_the_floor_scale_a_tenth_off_breaks_the_reading():
    # why the scale is set from the floor: the ground reader compares with the camera height
    from mana_perception.depth import read_ground

    cam = MOUNT.camera(COLS, ROWS, PITCH)
    assert read_ground(scene(syn.flat, scale=1.1), cam).ground != FLAT
    assert dz.read(scene(syn.flat, scale=1.1), MOUNT, PITCH).reading.ground == FLAT


# ---- what stands in the path


@pytest.mark.parametrize("at", [1.0, 2.0, 3.0])
def test_a_wall_ahead_is_measured(at):
    seen = dz.read(scene(wall(at), scale=1.15, noise=0.02), MOUNT, PITCH)
    assert seen.ahead_m == pytest.approx(at, abs=0.15)


def test_nothing_stands_in_the_path_of_a_floor_or_a_curb():
    assert dz.read(scene(syn.flat, noise=0.02), MOUNT, PITCH).ahead_m is None
    assert dz.read(scene(syn.step(1.8, 0.12)), MOUNT, PITCH).ahead_m is None


def test_the_distance_of_a_box_is_the_depth_in_its_middle():
    level = MOUNT.camera(COLS, ROWS, 0.0)  # level, so the distance ahead is the depth
    depth = np.full((ROWS, COLS), 5.0)
    depth[100:200, 150:200] = 2.0  # the object
    from demo.vision import Detection

    # the frame is 480 x 360; the box is a little wider than the object, as boxes are
    det = Detection(
        "person", 0.9, 150 * 480 / COLS, 90 * 360 / ROWS, 205 * 480 / COLS, 205 * 360 / ROWS
    )
    assert dz.box_distance(dz.Seen(depth, 1.0, None, cam=level), det, 480, 360) == 2.0
    # the model's own metres are not used for a distance: only once scaled to the floor
    assert dz.box_distance(dz.Seen(depth, None, None, cam=level), det, 480, 360) is None


def test_the_distance_of_a_box_is_along_the_floor_not_along_the_tilted_camera():
    seen = dz.read(scene(wall(2.5), scale=1.2), MOUNT, PITCH)
    from demo.vision import Detection

    on_the_wall = Detection("door", 0.9, 200, 20, 280, 160)  # above the middle of a 480 x 360 frame
    assert dz.box_distance(seen, on_the_wall, 480, 360) == pytest.approx(2.5, abs=0.1)


def test_the_depth_picture_is_a_jpeg():
    assert dz.colour(scene(syn.flat))[:2] == b"\xff\xd8"


# ---- through the server, with a stand-in for the model


class RenderedDepth:
    """Stands in for the depth model: always the same picture, 20 percent too far."""

    def __init__(self, ground):
        self.image = scene(ground, scale=1.2, noise=0.01)

    def estimate(self, jpeg):
        return self.image


def tilted_30(phone):
    g = 9.81
    return phone.send_json(
        {"type": "motion", "ax": 0.0, "ay": g * math.cos(PITCH), "az": g * math.sin(PITCH)}
    )


def test_a_drop_seen_by_the_camera_reaches_the_decision_core():
    pytest.importorskip("aiohttp")
    from mana_core.decision import Ground, Source
    from test_demo_server import jpeg, run, snapshot_where, with_clients

    async def go(client, phone, dash):
        await tilted_30(phone)
        await phone.send_bytes(b"\x01" + jpeg())
        snap = await snapshot_where(dash, lambda s: (s.get("depth") or {}).get("ground"))
        view = snap["depth"]
        assert view["ground"] == "drop" and view["distance_m"] == pytest.approx(2.0, abs=0.25)
        assert view["pitch_deg"] == 30 and view["scale"] == pytest.approx(1 / 1.2, abs=0.03)
        assert snap["models"]["depth"] is True
        held = client.app["mw"].core.ground[Source.CAMERA]
        assert held.ground == Ground.DROP  # the camera's own report, not a second sensor
        r = await client.get("/depth.jpg")
        assert r.status == 200 and (await r.read())[:2] == b"\xff\xd8"

    run(with_clients(go, depth=RenderedDepth(syn.step(2.0, -1.0)), mount=MOUNT))


class TwoPeople:
    """A near person and a far one, in a 480 x 360 frame."""

    def detect(self, jpeg):
        from demo.vision import Detection

        near = Detection("person", 0.9, 200, 20, 300, 340)
        far = Detection("person", 0.9, 20, 150, 50, 230)
        return [far, near], 480, 360


def test_the_core_hears_the_nearest_obstacle_of_a_frame_last():
    pytest.importorskip("aiohttp")
    from mana_core.decision import Source
    from test_demo_server import jpeg, run, with_clients

    async def go(client, phone, dash):
        await phone.send_bytes(b"\x01" + jpeg())
        mw = client.app["mw"]
        for _ in range(100):
            if Source.DETECTOR in mw.core.obstacles:
                break
            await asyncio.sleep(0.05)
        found = [d["distance_m"] for d in mw.detections]
        assert mw.core.obstacles[Source.DETECTOR].distance_m == pytest.approx(min(found), abs=0.01)

    run(with_clients(go, detector=TwoPeople()))


def vision_estimate():
    from demo.vision import Detection, to_obstacles

    return to_obstacles([Detection("person", 0.9, 200, 20, 280, 160)], 480, 360)[0].distance_m


def test_with_depth_the_obstacle_distance_is_measured_not_estimated():
    pytest.importorskip("aiohttp")
    from test_demo_server import jpeg, run, with_clients

    class OnePerson:
        def detect(self, jpeg):
            from demo.vision import Detection

            return [Detection("person", 0.9, 200, 20, 280, 160)], 480, 360

    class PersonAt3m:
        def estimate(self, jpeg):
            return scene(wall(3.0), scale=1.2)  # a person as wide as a wall, 3 m ahead

    async def go(client, phone, dash):
        mw = client.app["mw"]
        await tilted_30(phone)

        def person():
            return [d["distance_m"] for d in mw.detections if d["label"] == "person"]

        for _ in range(200):  # frames until one is analysed after the depth has arrived
            await phone.send_bytes(b"" + jpeg())
            await asyncio.sleep(0.05)
            if person() and person()[-1] == pytest.approx(3.0, abs=0.15):
                break
        assert person()[-1] == pytest.approx(3.0, abs=0.15)  # the box height says otherwise
        assert vision_estimate() != pytest.approx(3.0, abs=0.5)

    run(with_clients(go, detector=OnePerson(), depth=PersonAt3m(), mount=MOUNT))
