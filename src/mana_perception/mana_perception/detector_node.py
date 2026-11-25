"""ROS 2 node for the object detector on the camera image (M1 Vision).

Runs the same detector as the phone demo (mana_perception.detection) on each camera frame and
publishes the nearest obstacle as an ObstacleReport from the DETECTOR source. The core keeps one
report per source, so only the nearest is sent; when nothing is in view nothing is sent, and the
core lets the last report age out. The detector and the ground classifier both look through the
camera, so the core counts them as one sensor.

Topics (remap them to the camera's):
  camera/image        sensor_msgs/Image, rgb8, bgr8, rgba8 or bgra8; a frame stamped 0 gets the
                      time it arrived, or the core would see it as stale
  camera/camera_info  sensor_msgs/CameraInfo, for the focal length (distance) and the field of view
                      (bearing)
  obstacle            mana_msgs/ObstacleReport, source DETECTOR; trust is the detector's confidence
                      times camera_trust_factor of the frame's brightness, as on the phone, so the
                      camera is trusted less in the dark

Parameters:
  weights         Ultralytics weights (default yolo26n.pt, pretrained on COCO). yoloe-26n-mana.pt
                  from demo/build_detector.py adds stairs, doors and poles as labels; the
                  fine-tuned YOLO26s from #49 drops in here without a code change
  imgsz           size each frame is scaled to (default 416)
  conf            lowest confidence the detector keeps (default 0.3)
  min_confidence  lowest confidence that becomes an obstacle (default 0.5, the core's min_trust:
                  anything lower the core would ignore)
  rate_hz         most frames per second; frames in between are skipped (default 10.0)
  path_half_m     an obstacle counts when its nearer side is within this of the line straight
                  ahead (default 0.7: half the simulated chair's 0.8 m and 0.3 m); one beside the
                  path is not sent, so it neither stops the device nor hides one in the path
  glass_weights   the glass model (mana_perception.glass, tools/train_glass_seg.py --arch
                  pretrained); empty for none (default). Glass in the middle third for 3 frames
                  in a row is an obstacle labelled "glass", where it meets the floor
  camera_height_m the camera's height above the floor, for that distance (default 1.0, the
                  simulated chair's)
  camera_pitch_rad how far the camera looks below level (default 0.3, the simulated chair's)
"""

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image

from mana_msgs.msg import ObstacleReport
from mana_perception import detection, glass


class DetectorNode(Node):
    def __init__(self, detector=None, glass_model=None):
        super().__init__("mana_detector")
        weights = self.declare_parameter("weights", "yolo26n.pt").value
        imgsz = self.declare_parameter("imgsz", 416).value
        self.conf = self.declare_parameter("conf", 0.3).value
        self.min_confidence = self.declare_parameter("min_confidence", 0.5).value
        self.period_s = 1.0 / self.declare_parameter("rate_hz", 10.0).value
        self.path_half_m = self.declare_parameter("path_half_m", 0.7).value
        glass_weights = self.declare_parameter("glass_weights", "").value
        self.camera_height_m = self.declare_parameter("camera_height_m", 1.0).value
        self.camera_pitch_rad = self.declare_parameter("camera_pitch_rad", 0.3).value
        self.glass = glass_model or (glass.load(glass_weights) if glass_weights else None)
        if glass_weights and self.glass is None:
            self.get_logger().error(f"no glass model: check torchvision and {glass_weights!r}")
        self.in_a_row = glass.InARow()
        self.detector = detector or detection.Detector.load(weights, imgsz)
        if self.detector is None:
            self.get_logger().error(
                f"no detector: install ultralytics and check the weights {weights!r}"
            )
        self.info: CameraInfo | None = None
        self.last: Time | None = None
        self.warned: set[str] = set()
        self.create_subscription(CameraInfo, "camera/camera_info", self.on_info, 10)
        self.create_subscription(Image, "camera/image", self.on_image, 10)
        self.obstacle = self.create_publisher(ObstacleReport, "obstacle", 10)

    def on_info(self, m: CameraInfo) -> None:
        if m.k[0] > 0 and m.k[4] > 0:
            self.info = m

    def warn_once(self, text: str) -> None:
        if text not in self.warned:
            self.warned.add(text)
            self.get_logger().warning(text)

    def on_image(self, m: Image) -> None:
        if self.detector is None:
            return
        if self.info is None:
            self.warn_once("no camera info yet on camera/camera_info; skipping frames")
            return
        now = self.get_clock().now()
        if self.last is not None and (now - self.last).nanoseconds / 1e9 < self.period_s:
            return
        image = detection.to_rgb(m.encoding, m.height, m.width, m.step, m.data)
        if image is None:
            self.warn_once(f"image encoding {m.encoding!r} is not supported; use rgb8 or bgr8")
            return
        self.last = now
        found = self.detector.detect_image(image, self.conf)
        fx, fy = self.info.k[0], self.info.k[4]
        hfov = detection.hfov_from(fx, m.width)
        obstacles = detection.to_obstacles(
            found, m.width, m.height, fy, self.min_confidence, hfov, self.path_half_m
        )
        pane = self.glass_ahead(image, m.width, m.height, fy, hfov)
        if pane is not None:
            obstacles = sorted([*obstacles, pane], key=lambda o: o.distance_m)
        if not obstacles:
            return
        nearest = obstacles[0]
        light = detection.camera_trust_factor(detection.brightness(image))
        msg = ObstacleReport(
            trust=float(nearest.confidence * light),
            source=ObstacleReport.DETECTOR,
            label=nearest.label,
            distance_m=float(nearest.distance_m),
            bearing_rad=float(nearest.bearing_rad),
        )
        msg.header.frame_id = m.header.frame_id
        stamped = m.header.stamp.sec or m.header.stamp.nanosec
        msg.header.stamp = m.header.stamp if stamped else now.to_msg()
        self.obstacle.publish(msg)

    def glass_ahead(self, image, width, height, fy, hfov) -> detection.Obstacle | None:
        """Glass in the path as an obstacle, or None (see mana_perception.glass)."""
        if self.glass is None:
            return None
        ahead = glass.in_path(self.glass(image))
        sure = ahead is not None and ahead.confidence >= self.min_confidence
        if not self.in_a_row.see(sure):
            return None
        cy = self.info.k[5] if self.info.k[5] > 0 else height / 2.0
        row = ahead.bottom * height
        distance = glass.floor_distance(row, fy, cy, self.camera_height_m, self.camera_pitch_rad)
        if distance is None:  # its bottom edge above the horizon: not on the floor ahead
            return None
        bearing = detection.bearing_rad(ahead.centre * width, width, hfov)
        return detection.Obstacle("glass", ahead.confidence, distance, bearing)


def main(args=None):
    rclpy.init(args=args)
    node = DetectorNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
