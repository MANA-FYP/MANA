"""End to end: a camera frame reaches the core's topic as a GroundReport from the CLASSIFIER source.
A stand-in model gives a fixed class, since CI has no trained model; the class, the trust and the
stamp go through the same code as the real one."""

import math
import time

import numpy as np
import pytest
import rclpy
from mana_perception.classifier_node import ClassifierNode
from rclpy.executors import SingleThreadedExecutor
from sensor_msgs.msg import Image

from mana_msgs.msg import GroundReport
from mana_perception import detection

COLS, ROWS = 320, 240


class FixedModel:
    """Answers every frame with the same class, and remembers the frame it was given."""

    def __init__(self, label, probability):
        self.answer, self.frames = (label, probability), []

    def classify(self, rgb):
        self.frames.append(rgb)
        return self.answer


@pytest.fixture
def ros():
    # its own namespace: colcon runs the packages' tests side by side
    rclpy.init(args=["--ros-args", "-r", "__ns:=/classifier_node_test"])
    yield
    rclpy.shutdown()


def run(label, probability, encoding="bgr8", seconds=3.0, bgr=(200, 200, 255)):
    fake = FixedModel(label, probability)
    node, probe = ClassifierNode(model=fake), rclpy.create_node("probe")
    reports = []
    probe.create_subscription(GroundReport, "ground", reports.append, 10)
    image_pub = probe.create_publisher(Image, "camera/image", 10)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(probe)

    frame = np.zeros((ROWS, COLS, 3), dtype=np.uint8)
    frame[...] = bgr  # bright by default; as bgr8 the last channel is red
    image = Image(height=ROWS, width=COLS, encoding=encoding, is_bigendian=0, step=COLS * 3)
    image.data = frame.tobytes()
    image.header.frame_id = "camera"

    end = time.monotonic() + seconds
    while time.monotonic() < end and not (reports and fake.frames):
        image_pub.publish(image)
        executor.spin_once(timeout_sec=0.05)
    for n in (node, probe):
        n.destroy_node()
    return reports, fake


@pytest.mark.parametrize("label", ["to_stairs", "stairs", "to_level"])
def test_stairs_ahead_on_or_at_the_top_reach_the_core_as_stairs_up(ros, label):
    reports, fake = run(label, 0.9)
    assert reports, "no ground report was published"
    r = reports[0]
    assert r.source == GroundReport.CLASSIFIER
    assert r.ground == GroundReport.STAIRS_UP
    assert r.trust == pytest.approx(0.9)  # good light: the class's probability as it is
    assert math.isnan(r.distance_m) and math.isnan(r.step_height_m)  # not measured
    assert r.header.frame_id == "camera" and r.header.stamp.sec > 0  # a 0 stamp is replaced
    # the model saw the frame as RGB
    assert fake.frames[0].shape == (ROWS, COLS, 3) and tuple(fake.frames[0][0, 0]) == (
        255,
        200,
        200,
    )


def test_level_ground_reaches_the_core_as_flat(ros):
    reports, _ = run("level", 0.97)
    assert reports[0].ground == GroundReport.FLAT
    assert reports[0].trust == pytest.approx(0.97)


def test_in_the_dark_the_report_is_trusted_less_as_for_the_detector(ros):
    reports, _ = run("stairs", 0.9, bgr=(30, 30, 30))  # mean luminance 30 / 255
    light = detection.camera_trust_factor(30 / 255)
    assert 0 < light < 1
    assert reports[0].trust == pytest.approx(0.9 * light, abs=1e-3)


def test_an_unsupported_encoding_sends_nothing(ros):
    reports, fake = run("stairs", 0.9, encoding="mono8", seconds=1.0)
    assert reports == [] and fake.frames == []
