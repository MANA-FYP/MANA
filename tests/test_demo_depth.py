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


def test_no_scale_when_the_floor_is_not_in_view():
    up = math.radians(-20)  # looking up: the bottom row meets the floor about 10 m off
    cam = MOUNT.camera(COLS, ROWS, up)
    assert dz.floor_scale(scene(syn.flat, pitch=up), cam) is None
    seen = dz.Reader(MOUNT).read(scene(syn.flat, pitch=up), up)
    assert seen.reading is None and seen.why == "tilt the camera down to the floor"


def test_a_camera_held_nearly_level_still_sees_the_floor():
    # the real phone frames were taken 4 to 20 degrees down (docs/results.md, #131)
    low = math.radians(8)
    cam = MOUNT.camera(COLS, ROWS, low)
    assert dz.floor_scale(scene(syn.flat, pitch=low, scale=1.2), cam) * 1.2 == pytest.approx(
        1.0, abs=0.02
    )


@pytest.mark.parametrize(
    "pitch_deg, row, ahead", [(30, 132.5, 1.2 / math.tan(math.radians(30))), (0, 132.5, None)]
)
def test_the_floor_under_an_image_row(pitch_deg, row, ahead):
    cam = MOUNT.camera(COLS, ROWS, math.radians(pitch_deg))
    got = dz.floor_at(cam, row)  # the middle row looks along the optical axis
    assert got == (None if ahead is None else pytest.approx(ahead, abs=0.01))


def test_no_scale_when_the_bottom_of_the_picture_is_not_a_floor():
    cam = MOUNT.camera(COLS, ROWS, PITCH)
    rng = np.random.default_rng(1)
    clutter = rng.uniform(0.5, 4.0, (ROWS, COLS))  # a cluttered table, not a plane
    assert dz.floor_scale(clutter, cam) is None


# ---- the scale is learned from level floor, then held


def reader_on(pitch=PITCH, **error):
    """A reader that has learned its scale from three frames of level floor with this error."""
    r = dz.Reader(MOUNT)
    for seed in range(3):
        r.read(scene(syn.flat, pitch=pitch, seed=10 + seed, **error), pitch)
    assert r.held is not None
    return r


def test_no_reading_until_the_scale_is_learned_from_level_floor():
    r = dz.Reader(MOUNT)
    for _ in range(2):
        seen = r.read(scene(syn.flat, scale=1.2), PITCH)
        assert seen.reading is None and seen.why == "learning the scale: point at level floor"
    assert r.read(scene(syn.flat, scale=1.2), PITCH).reading is None  # the third one learns it
    assert r.held == pytest.approx(1 / 1.2, abs=0.01)
    assert r.read(scene(syn.flat, scale=1.2), PITCH).reading.ground == FLAT


def test_frames_with_an_edge_do_not_teach_the_scale():
    r = dz.Reader(MOUNT)
    for _ in range(5):
        r.read(scene(syn.step(2.0, -1.0)), PITCH)
    assert r.held is None


# Waqas's rows on #138: an edge closer than the bottom of the picture. Scaling each frame to its
# own bottom rows read every one of them as level floor.
BLIND = [(30, -0.17, 0.37), (30, -1.0, 0.37), (30, -0.5, 0.55), (45, -1.0, 0.15)]


@pytest.mark.parametrize("scale", [1.0, 1.2])
@pytest.mark.parametrize("pitch_deg, height, at", BLIND)
def test_an_edge_in_the_blind_zone_is_not_level_floor(pitch_deg, height, at, scale):
    pitch = math.radians(pitch_deg)
    r = reader_on(pitch, scale=scale)
    seen = r.read(scene(syn.step(at, height), pitch=pitch, scale=scale), pitch)
    assert seen.reading.ground in (CURB, DROP) and seen.reading.distance_m == 0.0


def test_with_the_held_scale_a_little_off_an_edge_down_is_still_a_drop_at_0_m():
    # the held scale 10 % off and the reader's own check not firing: the frame's own floor
    # scale, much smaller than the held one, still says the floor is lower
    r = reader_on()
    r.held *= 1.1
    seen = r.read(scene(syn.step(0.37, -1.0)), PITCH)
    assert (seen.reading.ground, seen.reading.distance_m) == (DROP, 0.0)


def test_a_new_scene_is_learned_again_after_walking_on():
    r = reader_on(scale=1.0)
    outdoors = scene(syn.flat, scale=1.4)  # the model reads this scene 40 % farther
    assert r.read(outdoors, PITCH, steps=0).reading.ground == DROP  # stop: it could be an edge
    assert r.read(outdoors, PITCH, steps=2).reading.ground == DROP  # still in the blind zone
    r.read(outdoors, PITCH, steps=4)  # four steps on, no edge was there: learn again
    for steps in (5, 6):
        r.read(outdoors, PITCH, steps=steps)
    assert r.held == pytest.approx(1 / 1.4, abs=0.02)
    assert r.read(outdoors, PITCH, steps=7).reading.ground == FLAT


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
    seen = reader_on(pitch, **error).read(scene(ground, pitch=pitch, **error), pitch)
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
    assert reader_on(scale=1.1).read(scene(syn.flat, scale=1.1), PITCH).reading.ground == FLAT


# ---- what stands in the path


@pytest.mark.parametrize("at", [1.0, 2.0, 3.0])
def test_a_wall_ahead_is_measured(at):
    seen = reader_on(scale=1.15, noise=0.02).read(scene(wall(at), scale=1.15, noise=0.02), PITCH)
    assert seen.ahead_m == pytest.approx(at, abs=0.15)


def test_nothing_stands_in_the_path_of_a_floor_or_a_curb():
    assert reader_on(noise=0.02).read(scene(syn.flat, noise=0.02), PITCH).ahead_m is None
    assert reader_on().read(scene(syn.step(1.8, 0.12)), PITCH).ahead_m is None


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
    seen = reader_on(scale=1.2).read(scene(wall(2.5), scale=1.2), PITCH)
    from demo.vision import Detection

    on_the_wall = Detection("door", 0.9, 200, 20, 280, 160)  # above the middle of a 480 x 360 frame
    assert dz.box_distance(seen, on_the_wall, 480, 360) == pytest.approx(2.5, abs=0.1)


def test_the_depth_picture_is_a_jpeg():
    assert dz.colour(scene(syn.flat))[:2] == b"\xff\xd8"


# ---- through the server, with a stand-in for the model


class RenderedDepth:
    """Stands in for the depth model: level floor for the first frames, then this ground, all
    20 percent too far."""

    def __init__(self, ground, flat_first=3):
        self.flat, self.image = scene(syn.flat, scale=1.2), scene(ground, scale=1.2, noise=0.01)
        self.left = flat_first

    def estimate(self, jpeg):
        self.left -= 1
        return self.flat if self.left >= 0 else self.image


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
        async def drain(ws):  # the phone reads what it is sent, or the server's sends back up
            async for _ in ws:
                pass

        reader = asyncio.create_task(drain(phone))
        await tilted_30(phone)
        for _ in range(100):  # frames until the scale is learned and the drop is read
            await phone.send_bytes(b"\x01" + jpeg())
            await asyncio.sleep(0.05)
            if (client.app["depth"].get("view") or {}).get("ground") == "drop":
                break
        snap = await snapshot_where(
            dash, lambda s: (s.get("depth") or {}).get("ground") == "drop", tries=400
        )
        view = snap["depth"]
        assert view["ground"] == "drop" and view["distance_m"] == pytest.approx(2.0, abs=0.25)
        assert view["pitch_deg"] == 30 and view["scale"] == pytest.approx(1 / 1.2, abs=0.03)
        assert snap["models"]["depth"] is True
        held = client.app["mw"].core.ground[Source.CAMERA]
        assert held.ground == Ground.DROP  # the camera's own report, not a second sensor
        r = await client.get("/depth.jpg")
        assert r.status == 200 and (await r.read())[:2] == b"\xff\xd8"
        reader.cancel()

    run(with_clients(go, depth=RenderedDepth(syn.step(2.0, -1.0)), mount=MOUNT, depth_ground=True))


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

    class PersonAt3m(RenderedDepth):
        def __init__(self):
            super().__init__(wall(3.0))  # a person as wide as a wall, 3 m ahead

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


def test_the_shape_of_the_depth_reaches_the_core_only_when_asked():
    pytest.importorskip("aiohttp")
    from mana_core.decision import Source
    from test_demo_server import jpeg, run, with_clients

    async def go(client, phone, dash):
        async def drain(ws):
            async for _ in ws:
                pass

        reader = asyncio.create_task(drain(phone))
        await tilted_30(phone)
        for _ in range(100):
            await phone.send_bytes(b"" + jpeg())
            await asyncio.sleep(0.05)
            if (client.app["depth"].get("view") or {}).get("ground") == "drop":
                break
        assert client.app["depth"]["view"]["ground"] == "drop"  # read and shown
        assert Source.CAMERA not in client.app["mw"].core.ground  # but not sent
        reader.cancel()

    run(with_clients(go, depth=RenderedDepth(syn.step(2.0, -1.0)), mount=MOUNT))


# ---- stairs: what from the detector's box, where from the floor, which way from the depth


def stairs_box(y2=300):
    from demo.vision import Detection

    return Detection("stairs", 0.6, 150, 120, 330, y2)


def test_stairs_are_where_the_box_meets_the_floor():
    ground, distance = dz.stairs_ground(stairs_box(300), 480, 360, MOUNT, PITCH)
    cam = MOUNT.camera(480, 360, PITCH)
    assert ground == STAIRS_UP and distance == pytest.approx(dz.floor_at(cam, 300))
    assert 1.0 < distance < 2.0
    _, unknown = dz.stairs_ground(stairs_box(300), 480, 360, MOUNT, None)  # no tilt: no distance
    assert math.isnan(unknown)


@pytest.mark.parametrize("rise, kind", [(0.17, STAIRS_UP), (-0.17, dz.dp.STAIRS_DOWN)])
def test_the_depth_says_whether_the_stairs_go_up_or_down(rise, kind):
    seen = reader_on(scale=1.2).read(scene(syn.stairs(1.5, rise), scale=1.2), PITCH)
    from demo.vision import Detection

    box = Detection("stairs", 0.6, 120, 40, 360, 250)
    assert dz.stairs_ground(box, 480, 360, MOUNT, PITCH, seen)[0] == kind


class StairsDetector:
    """The detector sees stairs whose bottom edge is at row 300 of a 480 x 360 frame."""

    def detect(self, jpeg):
        return [stairs_box(300)], 480, 360


def test_stairs_the_detector_sees_reach_the_core_with_their_distance():
    pytest.importorskip("aiohttp")
    from mana_core.decision import Ground, Source
    from test_demo_server import jpeg, run, with_clients

    async def go(client, phone, dash):
        await tilted_30(phone)
        mw = client.app["mw"]
        for _ in range(100):
            await phone.send_bytes(b"" + jpeg())
            await asyncio.sleep(0.05)
            if Source.DETECTOR in mw.core.ground:
                break
        held = mw.core.ground[Source.DETECTOR]
        expected = dz.floor_at(MOUNT.camera(480, 360, PITCH), 300)
        assert held.ground == Ground.STAIRS_UP
        assert held.distance_m == pytest.approx(expected, abs=0.6)  # less the sensor's offset

    run(with_clients(go, detector=StairsDetector(), mount=MOUNT))
