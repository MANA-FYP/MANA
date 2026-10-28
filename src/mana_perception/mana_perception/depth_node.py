"""ROS 2 node for the 3D camera ground reader (M1 Vision).

Reads the depth image and its camera info, and publishes one GroundReport per frame from the 3D
camera: flat, ramp, curb, stairs up, stairs down or a drop, with the distance to where the ground
changes and the step height. The work is in mana_perception.depth; this node only moves messages.

Topics (remap them to the camera's):
  depth/image        sensor_msgs/Image, 32FC1 in metres or 16UC1 in millimetres (REP 118); a
                     frame stamped 0 gets the time it arrived, or the core would see it as stale
  depth/camera_info  sensor_msgs/CameraInfo, for the focal lengths and the principal point
  motion             mana_msgs/MotionReport, the chair's pitch, added to the camera's tilt: on a
                     ramp or on the stairs the mounting tilt alone reads level ground as a step
  ground             mana_msgs/GroundReport, source DEPTH_CAMERA

Parameters:
  camera_height_m   optical centre above the ground (default 1.0)
  camera_pitch_rad  tilt below level, positive looking down (default 0.6)
  max_range_m       farthest ground read, in metres (default 3.0)
  rate_hz           most reports per second; extra frames are skipped (default 10.0)
  trust             trust given to a reading (default 0.9); UNKNOWN is always sent with trust 0
  motion_max_age_s  a motion report older than this is not used, and the tilt is the mounting's
                    alone (default 0.5, as the core's max_age_s)
"""

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image

from mana_msgs.msg import GroundReport, MotionReport
from mana_perception import depth as dp


class DepthNode(Node):
    def __init__(self):
        super().__init__("mana_depth_ground")
        self.height_m = self.declare_parameter("camera_height_m", 1.0).value
        self.pitch_rad = self.declare_parameter("camera_pitch_rad", 0.6).value
        self.settings = dp.Settings(max_range_m=self.declare_parameter("max_range_m", 3.0).value)
        self.period_s = 1.0 / self.declare_parameter("rate_hz", 10.0).value
        self.trust = self.declare_parameter("trust", 0.9).value
        self.motion_max_age_s = self.declare_parameter("motion_max_age_s", 0.5).value
        self.chair_pitch: tuple[float, Time] | None = None  # (pitch, when it arrived)
        self.cam: dp.Camera | None = None
        self.last: Time | None = None
        self.warned: set[str] = set()
        self.create_subscription(CameraInfo, "depth/camera_info", self.on_info, 10)
        self.create_subscription(Image, "depth/image", self.on_image, 10)
        self.create_subscription(MotionReport, "motion", self.on_motion, 10)
        self.ground = self.create_publisher(GroundReport, "ground", 10)

    def on_info(self, m: CameraInfo) -> None:
        fx, fy, cx, cy = m.k[0], m.k[4], m.k[2], m.k[5]
        if fx > 0 and fy > 0:
            self.cam = dp.Camera(fx, fy, cx, cy, self.height_m, self.pitch_rad)

    def on_motion(self, m: MotionReport) -> None:
        if m.trust >= 0.5 and m.pitch_rad == m.pitch_rad:  # trusted, and not NaN
            self.chair_pitch = (float(m.pitch_rad), self.get_clock().now())

    def pitch_now(self, now: Time) -> float:
        """The chair's pitch if a recent motion report gave it, else 0: the mounting tilt alone."""
        if self.chair_pitch is None:
            return 0.0
        pitch, at = self.chair_pitch
        return pitch if (now - at).nanoseconds / 1e9 <= self.motion_max_age_s else 0.0

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
        r = dp.read_ground(image, dp.tilted(self.cam, self.pitch_now(now)), self.settings)
        msg = GroundReport(
            trust=0.0 if r.ground == dp.UNKNOWN else self.trust,
            source=GroundReport.DEPTH_CAMERA,
            ground=r.ground,
            distance_m=r.distance_m,
            step_height_m=r.step_height_m,
        )
        msg.header.frame_id = m.header.frame_id
        stamped = m.header.stamp.sec or m.header.stamp.nanosec
        # a frame stamped 0 (1970) would make the core drop every report as stale
        msg.header.stamp = m.header.stamp if stamped else now.to_msg()
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
