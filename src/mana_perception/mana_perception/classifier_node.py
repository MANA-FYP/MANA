"""ROS 2 node for the camera's ground classifier (M1 Vision, #38).

Runs the StairNet classifier (mana_perception.ground_classifier) on camera frames and publishes a
GroundReport from the CLASSIFIER source: FLAT for level ground, STAIRS_UP for stairs ahead, on
them, or at their top. It is the camera's half of the two-source rule for stairs; the 3D camera
(depth_node) is the other half. The classifier and the object detector both look through the
camera, so the core counts them as one sensor. The classifier cannot tell how far the stairs are,
so distance_m and step_height_m are NaN.

Topics (remap them to the camera's):
  camera/image  sensor_msgs/Image, rgb8, bgr8, rgba8 or bgra8; a frame stamped 0 gets the time it
                arrived, or the core would see it as stale
  ground        mana_msgs/GroundReport, source CLASSIFIER; trust is the class's probability times
                camera_trust_factor of the frame's brightness, as for the detector, so the camera
                is trusted less in the dark

Parameters:
  model    ONNX file from tools/train_ground.py (default ground.onnx)
  rate_hz  most frames per second; frames in between are skipped (default 5.0)
"""

import math

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import Image

from mana_msgs.msg import GroundReport
from mana_perception import detection, ground_classifier


class ClassifierNode(Node):
    def __init__(self, model=None):
        super().__init__("mana_ground_classifier")
        path = self.declare_parameter("model", "ground.onnx").value
        self.period_s = 1.0 / self.declare_parameter("rate_hz", 5.0).value
        log = self.get_logger().error
        self.model = model or ground_classifier.GroundModel.load(path, log=log)
        if self.model is None:
            log(f"no ground classifier: install onnxruntime and check the model {path!r}")
        self.last: Time | None = None
        self.warned: set[str] = set()
        self.create_subscription(Image, "camera/image", self.on_image, 10)
        self.ground = self.create_publisher(GroundReport, "ground", 10)

    def warn_once(self, text: str) -> None:
        if text not in self.warned:
            self.warned.add(text)
            self.get_logger().warning(text)

    def on_image(self, m: Image) -> None:
        if self.model is None:
            return
        now = self.get_clock().now()
        if self.last is not None and (now - self.last).nanoseconds / 1e9 < self.period_s:
            return
        image = detection.to_rgb(m.encoding, m.height, m.width, m.step, m.data)
        if image is None:
            self.warn_once(f"image encoding {m.encoding!r} is not supported; use rgb8 or bgr8")
            return
        self.last = now
        label, probability = self.model.classify(image)
        light = detection.camera_trust_factor(detection.brightness(image))
        msg = GroundReport(
            trust=float(probability * light),
            source=GroundReport.CLASSIFIER,
            ground=ground_classifier.to_ground(label),
            distance_m=math.nan,
            step_height_m=math.nan,
        )
        msg.header.frame_id = m.header.frame_id
        stamped = m.header.stamp.sec or m.header.stamp.nanosec
        msg.header.stamp = m.header.stamp if stamped else now.to_msg()
        self.ground.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = ClassifierNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
