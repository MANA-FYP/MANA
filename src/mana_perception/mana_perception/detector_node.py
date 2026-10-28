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
"""

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image

from mana_msgs.msg import ObstacleReport
from mana_perception import detection


class DetectorNode(Node):
    def __init__(self, detector=None):
        super().__init__("mana_detector")
        weights = self.declare_parameter("weights", "yolo26n.pt").value
        imgsz = self.declare_parameter("imgsz", 416).value
        self.conf = self.declare_parameter("conf", 0.3).value
        self.min_confidence = self.declare_parameter("min_confidence", 0.5).value
        self.period_s = 1.0 / self.declare_parameter("rate_hz", 10.0).value
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
        obstacles = detection.to_obstacles(
            found, m.width, m.height, fy, self.min_confidence, detection.hfov_from(fx, m.width)
        )
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
