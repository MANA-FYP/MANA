"""demo/doorsign.py: push or pull from the words read, and reading only around the door."""

import io

import pytest
from PIL import Image

from demo.doorsign import DoorSign, way


def test_a_push_or_pull_sign_says_which_way():
    assert way([("PUSH", 0.9)]) == ("push", 0.9)
    assert way([("Pull", 0.8), ("Exit", 0.9)]) == ("pull", 0.8)
    assert way([("PUSH→", 0.7), ("push", 0.95)]) == ("push", 0.95)  # the surer reading


def test_no_sign_both_words_or_a_doubtful_reading_say_nothing():
    assert way([]) == (None, 0.0)
    assert way([("EXIT", 0.9)]) == (None, 0.0)
    assert way([("PUSH", 0.9), ("PULL", 0.9)]) == (None, 0.0)  # does not say which
    assert way([("PULL", 0.3)]) == (None, 0.0)  # below MIN_SCORE
    assert way([("pushchairs", 0.9)]) == (None, 0.0)  # a word containing it is not the sign


class Engine:
    """Stands in for RapidOCR: says PULL, and keeps the size of what it was given."""

    def __init__(self):
        self.shapes = []

    def __call__(self, image):
        self.shapes.append(image.shape)
        return [[[[0, 0], [1, 0], [1, 1], [0, 1]], "PULL", 0.93]], 0.01


def jpeg(w=400, h=300):
    out = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(out, "JPEG")
    return out.getvalue()


def test_only_the_door_and_a_margin_round_it_are_read():
    engine = Engine()
    sign = DoorSign(engine)
    assert sign.read(jpeg()) == ("pull", 0.93)
    assert engine.shapes[-1][:2] == (300, 400)
    sign.read(jpeg(), box=(100, 50, 200, 250))  # 100 x 200, grown 25% each way
    assert engine.shapes[-1][:2] == (300, 150)  # 0..300 high (clipped), 75..225 wide


def test_the_way_a_door_opens_is_said_once_and_a_pull_door_warns_a_chair_user():
    from demo.middleware import Middleware

    chair, foot = Middleware(), Middleware(on_foot=True)
    for mw in (chair, foot):
        mw.on_door_sign("pull", 1.0)
        mw.on_door_sign("pull", 5.0)  # the same door, still in view: not said again
    said = [d["text"] for d in chair.decisions if d["kind"] == "door"]
    assert said == ["Door ahead. It opens towards you: pull. You may need help."]
    assert [d["text"] for d in foot.decisions if d["kind"] == "door"] == [
        "Door ahead. Pull to open."
    ]


class Reader:
    def __init__(self):
        self.boxes = []

    def read(self, jpeg, box=None):
        self.boxes.append(box)
        return "push", 0.9


def test_the_server_reads_the_largest_door_at_most_once_a_second():
    import time

    from demo import server
    from demo.vision import Detection

    reader = Reader()
    app = server.build_app(door_signs=reader)
    small = Detection("door", 0.8, 0, 0, 10, 10)
    big = Detection("glass door", 0.6, 100, 50, 300, 400)
    for now in (10.0, 10.3, 11.2):
        server.door_sign(app, jpeg(), [small, big, Detection("person", 0.9, 0, 0, 50, 50)], now)
        for _ in range(100):  # the reading runs in its own thread
            if not app["live"]["door_reading"]:
                break
            time.sleep(0.01)
    assert reader.boxes == [(100, 50, 300, 400)] * 2  # 10.0 and 11.2; 10.3 is too soon
    assert any(
        d["text"] == "Door ahead. It opens away from you: push." for d in app["mw"].decisions
    )
    # no door boxed surely enough: the whole frame, every FRAME_READ_S
    for now in (20.0, 21.0, 22.1):
        server.door_sign(app, jpeg(), [Detection("door", 0.1, 0, 0, 50, 50)], now)
        for _ in range(100):
            if not app["live"]["door_reading"]:
                break
            time.sleep(0.01)
    assert reader.boxes[2:] == [None, None]  # 20.0 and 22.1; 21.0 is too soon


def test_a_letter_height_in_pixels_is_a_distance_for_a_5_cm_letter():
    import numpy as np

    from tools import door_sign_sizes as ds

    f = 320 / np.tan(np.radians(33))  # 640 pixels across 66 degrees
    assert ds.metres(10) == pytest.approx(f * 0.05 / 10)  # about 2.5 m
    assert ds.metres(20) == pytest.approx(ds.metres(10) / 2)


def test_only_a_door_filling_a_tenth_of_the_picture_is_used():
    import numpy as np

    from tools import door_sign_sizes as ds

    seg = np.zeros((100, 100), np.uint8)
    seg[10:60, 20:40] = ds.DOOR  # a tenth exactly
    assert ds.door_box(seg) == (20, 10, 39, 59)
    seg[10:60, 39] = 0
    assert ds.door_box(seg) is None


class SignBesideTheDoor:
    """Nothing in the boxed door; PULL somewhere else in the frame."""

    def __init__(self):
        self.boxes = []

    def read(self, jpeg, box=None):
        self.boxes.append(box)
        return (None, 0.0) if box is not None else ("pull", 0.9)


def test_a_sign_outside_the_boxed_door_is_read_from_the_whole_frame():
    import time

    from demo import server
    from demo.vision import Detection

    reader = SignBesideTheDoor()
    app = server.build_app(door_signs=reader)
    server.door_sign(app, jpeg(), [Detection("door", 0.8, 10, 10, 100, 200)], 5.0)
    for _ in range(100):
        if not app["live"]["door_reading"]:
            break
        time.sleep(0.01)
    assert reader.boxes == [(10, 10, 100, 200), None]
    assert [d["text"] for d in app["mw"].decisions if d["kind"] == "door"] == [
        "Door ahead. It opens towards you: pull. You may need help."
    ]


def test_a_door_sign_does_not_drop_a_siren_that_waits_to_be_said():
    """Both are a notice, not a decision: before, the door line, pushed as a decision, dropped
    every waiting message below a stop, a siren's warning among them."""
    from demo.middleware import Middleware

    mw = Middleware()
    mw.on_sounds({"siren": 0.6}, now=1.0)
    mw.on_door_sign("push", 1.1)
    waiting = [m.text for m in mw.queue.waiting]
    assert "A siren nearby. Take care." in waiting
    assert any(w.startswith("Door ahead") for w in waiting)
