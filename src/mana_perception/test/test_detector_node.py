"""End to end: a camera frame with people in it reaches the core's topic as one ObstacleReport, the
nearest, from the DETECTOR source. A stand-in detector gives fixed boxes, since CI has no
Ultralytics; the boxes, the distance and the bearing go through the same code as the real model."""

import math
import time

import numpy as np
import pytest
import rclpy
from mana_perception.detector_node import DetectorNode
from rclpy.executors import SingleThreadedExecutor
from sensor_msgs.msg import CameraInfo, Image

from mana_msgs.msg import ObstacleReport
from mana_perception import detection

COLS, ROWS = 640, 480
F = 500.0  # focal length in pixels


class FixedDetector:
    """Answers every frame with the same boxes, and remembers the frame it was given."""

    def __init__(self, boxes):
        self.boxes, self.frames = boxes, []

    def detect_image(self, image, conf=0.3):
        self.frames.append(image)
        return list(self.boxes)


# a person 1.7 m tall, 250 px high, left of centre: 500 * 1.7 / 250 = 3.4 m away
NEAR = detection.Detection("person", 0.8, 100, 100, 180, 350)
# a person 125 px high on the right: 6.8 m away
FAR = detection.Detection("person", 0.9, 500, 100, 540, 225)
# a door has no height, so it is a label only, and never an obstacle
DOOR = detection.Detection("door", 0.95, 300, 0, 400, 470)


@pytest.fixture
def ros():
    # its own namespace: colcon runs the packages' tests side by side
    rclpy.init(args=["--ros-args", "-r", "__ns:=/detector_node_test"])
    yield
    rclpy.shutdown()


def run(
    boxes, encoding="bgr8", seconds=3.0, bgr=(200, 200, 255), glass_model=None, want=1, path=None
):
    """path: the node's path_half_m; None (the default here) sends obstacles beside the path too,
    as these tests place people left and right to check the bearing and the distance."""
    fake = FixedDetector(boxes)
    node = DetectorNode(detector=fake, glass_model=glass_model)
    node.path_half_m = path
    probe = rclpy.create_node("probe")
    reports = []
    probe.create_subscription(ObstacleReport, "obstacle", reports.append, 10)
    info_pub = probe.create_publisher(CameraInfo, "camera/camera_info", 10)
    image_pub = probe.create_publisher(Image, "camera/image", 10)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(probe)

    info = CameraInfo(width=COLS, height=ROWS)
    info.k = [F, 0.0, COLS / 2.0, 0.0, F, ROWS / 2.0, 0.0, 0.0, 1.0]
    frame = np.zeros((ROWS, COLS, 3), dtype=np.uint8)
    frame[...] = bgr  # bright by default; as bgr8 the last channel is red
    image = Image(height=ROWS, width=COLS, encoding=encoding, is_bigendian=0, step=COLS * 3)
    image.data = frame.tobytes()
    image.header.frame_id = "camera"

    end = time.monotonic() + seconds
    while time.monotonic() < end and not (len(reports) >= want and fake.frames):
        info_pub.publish(info)
        image_pub.publish(image)
        executor.spin_once(timeout_sec=0.05)
    for n in (node, probe):
        n.destroy_node()
    return reports, fake


def test_the_nearest_obstacle_in_a_frame_reaches_the_core_from_the_detector(ros):
    reports, fake = run([FAR, DOOR, NEAR])
    assert reports, "no obstacle report was published"
    r = reports[0]
    assert r.source == ObstacleReport.DETECTOR
    assert r.label == "person"
    assert r.distance_m == pytest.approx(3.4, abs=0.01)
    assert r.bearing_rad > 0  # left of centre is positive (REP 103)
    assert r.trust == pytest.approx(0.8)  # good light: the detector's confidence as it is
    assert r.header.frame_id == "camera" and r.header.stamp.sec > 0  # a 0 stamp is replaced
    # the detector saw the frame as RGB
    assert fake.frames[0].shape == (ROWS, COLS, 3) and tuple(fake.frames[0][0, 0]) == (
        255,
        200,
        200,
    )


def test_labels_without_a_height_are_not_obstacles(ros):
    reports, fake = run([DOOR], seconds=1.0)
    assert fake.frames, "the detector never saw a frame"
    assert reports == []


def test_the_bearing_follows_the_camera_field_of_view(ros):
    reports, _ = run([FAR])
    hfov = 2 * math.atan((COLS / 2) / F)
    offset = ((500 + 540) / 2 - COLS / 2) / (COLS / 2)
    assert reports[0].bearing_rad == pytest.approx(-offset * hfov / 2, abs=1e-4)


def test_in_the_dark_the_report_is_trusted_less_as_on_the_phone(ros):
    reports, _ = run([NEAR], bgr=(30, 30, 30))  # mean luminance 30 / 255
    light = detection.camera_trust_factor(30 / 255)
    assert 0 < light < 1
    assert reports[0].trust == pytest.approx(0.8 * light, abs=1e-3)


def glass_ahead(image):
    """A stand-in glass model: glass over the middle of the picture, down to 3/4 of its height."""
    from mana_perception.glass import SIZE

    m = np.zeros((SIZE, SIZE), np.float32)
    m[: SIZE * 3 // 4, SIZE // 3 : 2 * SIZE // 3] = 0.9
    return m


def test_glass_in_the_path_is_an_obstacle_where_it_meets_the_floor(ros):
    reports, _ = run([FAR], glass_model=glass_ahead, want=4, seconds=6.0)
    labels = [r.label for r in reports]
    assert "glass" in labels and labels.index("glass") >= 2  # not before its third frame
    g = reports[labels.index("glass")]
    # its bottom at row 360 of 480, 120 rows below the centre: from 1 m up, tilted 0.3 rad down
    yn = 120 / F
    expected = (math.cos(0.3) - yn * math.sin(0.3)) / (yn * math.cos(0.3) + math.sin(0.3))
    assert g.distance_m == pytest.approx(expected, abs=0.05)
    assert abs(g.bearing_rad) < 0.05 and g.source == ObstacleReport.DETECTOR
    assert all(r.label == "person" for r in reports[: labels.index("glass")])  # the far person


def test_without_a_glass_model_nothing_changes(ros):
    reports, _ = run([FAR], want=3, seconds=3.0)
    assert reports and all(r.label == "person" for r in reports)


def test_only_an_obstacle_in_the_path_is_sent_not_a_nearer_one_beside_it(ros):
    """Someone 3.4 m away whose nearer side is 0.9 m left of the line ahead is beside the chair's
    path (0.7 m: half its width and a margin); the person straight ahead at 6.8 m is sent, since
    the core keeps one obstacle per source and the one beside must not hide it."""
    ahead = detection.Detection("person", 0.9, 300, 100, 340, 225)  # spans the middle: 6.8 m
    reports, _ = run([NEAR, ahead], path=0.7)
    assert reports and reports[0].distance_m == pytest.approx(6.8, abs=0.01)
    reports, _ = run([NEAR], path=0.7, seconds=1.0)
    assert reports == []
