"""End to end: a depth frame of stairs reaches the core's topic as a 3D camera stairs report."""

import time

import numpy as np
import pytest
import rclpy
from mana_perception.depth_node import DepthNode
from rclpy.executors import SingleThreadedExecutor
from sensor_msgs.msg import CameraInfo, Image

from mana_msgs.msg import GroundReport
from mana_perception import synthetic as syn

CAM = syn.CAMERA


def stairs_depth():
    """Stairs up 1.5 m ahead, in millimetres as a 16UC1 camera sends them."""
    metres = syn.render(syn.stairs(1.5, 0.17))
    return np.where(np.isfinite(metres), metres * 1000, 0).astype(np.uint16)  # 0: no reading


@pytest.fixture
def ros():
    # its own namespace: colcon runs the packages' tests side by side, and the scenario node test
    # publishes on ground too
    rclpy.init(args=["--ros-args", "-r", "__ns:=/depth_node_test"])
    yield
    rclpy.shutdown()


@pytest.mark.parametrize("stamped", [True, False], ids=["stamped", "stamp-0"])
def test_stairs_in_a_depth_frame_are_reported_by_the_3d_camera(ros, stamped):
    node, probe = DepthNode(), rclpy.create_node("probe")
    reports = []
    probe.create_subscription(GroundReport, "ground", reports.append, 10)
    info_pub = probe.create_publisher(CameraInfo, "depth/camera_info", 10)
    image_pub = probe.create_publisher(Image, "depth/image", 10)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(probe)

    info = CameraInfo(width=syn.COLS, height=syn.ROWS)
    info.k = [CAM.fx, 0.0, CAM.cx, 0.0, CAM.fy, CAM.cy, 0.0, 0.0, 1.0]
    frame = stairs_depth()
    image = Image(height=syn.ROWS, width=syn.COLS, encoding="16UC1", is_bigendian=0)
    image.step = syn.COLS * 2
    image.data = frame.astype("<u2").tobytes()
    image.header.frame_id = "depth_camera"
    if stamped:
        image.header.stamp.sec, image.header.stamp.nanosec = 1_700_000_000, 5

    end = time.monotonic() + 5.0
    while not reports and time.monotonic() < end:
        info_pub.publish(info)
        image_pub.publish(image)
        executor.spin_once(timeout_sec=0.05)
    for n in (node, probe):
        n.destroy_node()

    assert reports, "no ground report was published"
    r = reports[0]
    assert r.source == GroundReport.DEPTH_CAMERA
    assert r.ground == GroundReport.STAIRS_UP
    assert abs(r.distance_m - 1.5) < 0.1
    assert r.trust == pytest.approx(0.9)
    assert r.header.frame_id == "depth_camera"
    if stamped:  # the frame's own stamp is kept
        assert (r.header.stamp.sec, r.header.stamp.nanosec) == (1_700_000_000, 5)
    else:  # a camera that leaves the stamp at 0 still gives reports the core will not drop
        assert r.header.stamp.sec > 0
