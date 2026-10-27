"""ROS 2 node for the 3D camera ground reader (M1 Vision).

Reads the depth image and its camera info, and publishes one GroundReport per frame from the 3D
camera: flat, ramp, curb, stairs up, stairs down or a drop, with the distance to where the ground
changes and the step height. The work is in mana_perception.depth; this node only moves messages.

Topics (remap them to the camera's):
  depth/image        sensor_msgs/Image, 32FC1 in metres or 16UC1 in millimetres (REP 118)
  depth/camera_info  sensor_msgs/CameraInfo, for the focal lengths and the principal point
  ground             mana_msgs/GroundReport, source DEPTH_CAMERA

Parameters:
  camera_height_m   optical centre above the ground (default 1.0)
  camera_pitch_rad  tilt below level, positive looking down (default 0.6)
  max_range_m       farthest ground read, in metres (default 3.0)
  rate_hz           most reports per second; extra frames are skipped (default 10.0)
  trust             trust given to a reading (default 0.9); UNKNOWN is always sent with trust 0
"""

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image

from mana_msgs.msg import GroundReport
from mana_perception import depth as dp


class DepthNode(Node):
    def __init__(self):
        super().__init__("mana_depth_ground")
        self.height_m = self.declare_parameter("camera_height_m", 1.0).value
        self.pitch_rad = self.declare_parameter("camera_pitch_rad", 0.6).value
        self.settings = dp.Settings(max_range_m=self.declare_parameter("max_range_m", 3.0).value)
        self.period_s = 1.0 / self.declare_parameter("rate_hz", 10.0).value
        self.trust = self.declare_parameter("trust", 0.9).value
        self.cam: dp.Camera | None = None
        self.last: Time | None = None
        self.warned: set[str] = set()
        self.create_subscription(CameraInfo, "depth/camera_info", self.on_info, 10)
        self.create_subscription(Image, "depth/image", self.on_image, 10)
        self.ground = self.create_publisher(GroundReport, "ground", 10)

    def on_info(self, m: CameraInfo) -> None:
        fx, fy, cx, cy = m.k[0], m.k[4], m.k[2], m.k[5]
        if fx > 0 and fy > 0:
            self.cam = dp.Camera(fx, fy, cx, cy, self.height_m, self.pitch_rad)

    def warn_once(self, text: str) -> None:
        if text not in self.warned:
            self.warned.add(text)
            self.get_logger().warning(text)

    def on_image(self, m: Image) -> None:
        if self.cam is None:
            self.warn_once("no camera info yet on depth/camera_info; skipping depth frames")
            return
        now = self.get_clock().now()  # not the frame's stamp, which some cameras leave at 0
        if self.last is not None and (now - self.last).nanoseconds / 1e9 < self.period_s:
            return
        image = dp.to_metres(m.encoding, m.height, m.width, m.step, m.data, bool(m.is_bigendian))
        if image is None:
            self.warn_once(f"depth encoding {m.encoding!r} is not supported; use 32FC1 or 16UC1")
            return
        self.last = now
        r = dp.read_ground(image, self.cam, self.settings)
        msg = GroundReport(
            trust=0.0 if r.ground == dp.UNKNOWN else self.trust,
            source=GroundReport.DEPTH_CAMERA,
            ground=r.ground,
            distance_m=r.distance_m,
            step_height_m=r.step_height_m,
        )
        msg.header = m.header
        self.ground.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = DepthNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
