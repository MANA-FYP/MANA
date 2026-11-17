"""Any camera as the vision input (#132): a video file stands in for the webcam, and its frames
reach the demo server as the phone's do. Needs OpenCV and aiohttp; skipped without them."""

import asyncio
import importlib.util
import math

import numpy as np
import pytest

from demo import camera

# A mark, not pytest.importorskip at module level: in CI, ROS's launch_testing plugin imports each
# test module itself, and a skip raised there skipped the whole session (0 tests collected).
HAVE = all(importlib.util.find_spec(m) for m in ("cv2", "aiohttp"))
pytestmark = pytest.mark.skipif(not HAVE, reason="needs OpenCV and aiohttp")
if HAVE:
    import cv2
    from aiohttp.test_utils import TestServer

    from demo.server import build_app


def video(path, n=10, size=(320, 240)):
    """A small video file: n frames, each a different shade."""
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, size)
    for i in range(n):
        out.write(np.full((size[1], size[0], 3), 20 * i, np.uint8))
    out.release()
    return path


def test_the_tilt_is_sent_as_the_phone_would_read_it():
    upright, down = camera.gravity(0.0), camera.gravity(30.0)
    assert (upright["ay"], upright["az"]) == pytest.approx((9.81, 0.0))
    assert math.degrees(math.asin(down["az"] / 9.81)) == pytest.approx(30.0)


def test_the_tilt_has_no_default_it_is_given_or_turned_off(capsys):
    # a default tilt that is wrong turns stairs up into stairs down with no warning (#153)
    with pytest.raises(SystemExit) as stop:
        camera.main(["--source", "no-such-file.mp4"])
    assert stop.value.code == 2 and "--pitch" in capsys.readouterr().err
    with pytest.raises(SystemExit) as stop:  # not both
        camera.main(["--pitch", "10", "--no-pitch"])
    assert stop.value.code == 2
    with pytest.raises(SystemExit, match="cannot open"):  # given: on to the camera
        camera.main(["--source", "no-such-file.mp4", "--pitch", "10"])


def test_a_frame_is_sent_480_pixels_across_as_a_jpeg():
    jpeg = camera.jpeg_of(np.zeros((720, 1280, 3), np.uint8))
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert jpeg[:2] == b"\xff\xd8" and image.shape[:2] == (270, 480)


def test_a_file_plays_once_or_again_and_again(tmp_path):
    path = video(tmp_path / "walk.avi", n=4)
    assert len(list(camera.frames(camera.open_source(str(path)), loop=False))) == 4
    again = camera.frames(camera.open_source(str(path)), loop=True)
    assert len([next(again) for _ in range(9)]) == 9


def test_a_missing_camera_says_so():
    with pytest.raises(SystemExit, match="cannot open"):
        camera.open_source("no-such-file.mp4")


def test_a_video_file_reaches_the_server_like_the_phone_camera(tmp_path):
    path = video(tmp_path / "walk.avi", n=6)

    async def go():
        app = build_app()
        server = TestServer(app)
        await server.start_server()
        try:
            url = str(server.make_url("")).rstrip("/")
            sent = await camera.stream(url, camera.open_source(str(path)), 30.0, fps=50)
            for _ in range(40):  # the server's loop makes the snapshot a few times a second
                snap = app["live"]["snapshot"]
                if (
                    snap
                    and next(s for s in snap["sensors"] if s["id"] == "camera")["state"] == "active"
                ):
                    break
                await asyncio.sleep(0.05)
            return sent, app["frame"]["seq"], snap
        finally:
            await server.close()

    sent, seq, snap = asyncio.run(go())
    assert sent == 6 and seq == 6
    assert next(s for s in snap["sensors"] if s["id"] == "camera")["state"] == "active"
    motion = next(s for s in snap["sensors"] if s["id"] == "motion")
    assert motion["state"] == "active"  # the tilt arrived as a motion reading
