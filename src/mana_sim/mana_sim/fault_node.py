"""ROS 2 node: puts the sensor faults between the bridge and the rest of MANA.

The bridge publishes every faultable sensor on a *_raw topic. This node republishes it on the topic
the others read (camera/image, scan, imu, ...), unchanged while no fault is on. A fault is switched
with the service set_fault (mana_msgs/srv/SetFault), for example:

    ros2 service call /set_fault mana_msgs/srv/SetFault "{sensor: gps, kind: dropout, active: true}"

`fault_state` (a String, latched) says which faults are on. The node also sends the heartbeats
of the two radios, once a second, on `link/wifi/heartbeat` and `link/bluetooth/heartbeat`, which
mana_connectivity's link_node turns into `link`. A Wi-Fi fade loses more and more of the Wi-Fi
heartbeats until none arrives. Run it on simulation time.
"""

import random

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from sensor_msgs.msg import CameraInfo, Image, Imu, JointState, LaserScan, NavSatFix
from std_msgs.msg import Header, String

from mana_msgs.srv import SetFault
from mana_sim import faults as f


class FaultNode(Node):
    def __init__(self) -> None:
        super().__init__("fault_node")
        seed = self.declare_parameter("seed", 1).value
        self.rng = random.Random(seed)
        self.np_rng = np.random.default_rng(seed)
        self.faults = f.Faults()

        self.state = self.create_publisher(
            String, "fault_state", QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        )
        self.wifi_beat = self.create_publisher(Header, "link/wifi/heartbeat", 10)
        self.bluetooth_beat = self.create_publisher(Header, "link/bluetooth/heartbeat", 10)
        self.create_service(SetFault, "set_fault", self.on_set_fault)
        self.create_timer(1.0, self.send_beats)

        # raw topic -> (topic read by the others, type, sensor, what to do with a message)
        for raw, out, kind, sensor, fix in (
            ("camera/image_raw", "camera/image", Image, "camera", self.fix_camera),
            ("camera/camera_info_raw", "camera/camera_info", CameraInfo, "camera", None),
            ("depth/image_raw", "depth/image", Image, "depth", self.fix_depth),
            ("depth/camera_info_raw", "depth/camera_info", CameraInfo, "depth", None),
            ("scan_raw", "scan", LaserScan, "lidar", self.fix_scan),
            ("imu_raw", "imu", Imu, "imu", self.fix_imu),
            ("gps/fix_raw", "gps/fix", NavSatFix, "gps", self.fix_gps),
            ("joint_states_raw", "joint_states", JointState, "wheels", self.fix_joints),
        ):
            self.relay(raw, out, kind, sensor, fix)
        self.publish_state()

    def relay(self, raw, out, kind, sensor, fix) -> None:
        pub = self.create_publisher(kind, out, 10)

        def forward(m):
            if self.faults.dropped(sensor):
                return
            pub.publish(fix(m) if fix else m)

        self.create_subscription(kind, raw, forward, 10)

    def now_s(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def on_set_fault(self, req: SetFault.Request, res: SetFault.Response) -> SetFault.Response:
        res.ok, res.message = self.faults.set(
            req.sensor, req.kind, req.active, req.value, self.now_s()
        )
        if res.ok:
            self.get_logger().info(f"faults on: {res.message}")
            self.publish_state()
        return res

    def publish_state(self) -> None:
        self.state.publish(String(data=self.faults.summary()))

    def send_beats(self) -> None:
        beat = Header(stamp=self.get_clock().now().to_msg())
        self.bluetooth_beat.publish(beat)
        if self.rng.random() < self.faults.wifi_quality(self.now_s()):
            self.wifi_beat.publish(beat)

    def fix_camera(self, m: Image) -> Image:
        data = f.noisy_image(
            bytes(m.data), m.encoding, self.faults.noise_sigma("camera"), self.np_rng
        )
        m.data = f.darkened(data, self.faults.light_left())
        return m

    def fix_depth(self, m: Image) -> Image:
        m.data = f.noisy_image(
            bytes(m.data), m.encoding, self.faults.noise_sigma("depth"), self.np_rng
        )
        return m

    def fix_scan(self, m: LaserScan) -> LaserScan:
        m.ranges = f.noisy(list(m.ranges), self.faults.noise_sigma("lidar"), self.rng)
        return m

    def fix_imu(self, m: Imu) -> Imu:
        sigma = self.faults.noise_sigma("imu")
        if sigma > 0.0:
            w, a = m.angular_velocity, m.linear_acceleration
            w.x, w.y, w.z = f.noisy([w.x, w.y, w.z], sigma, self.rng)
            a.x, a.y, a.z = f.noisy([a.x, a.y, a.z], sigma, self.rng)
        return m

    def fix_gps(self, m: NavSatFix) -> NavSatFix:
        sigma = self.faults.noise_sigma("gps")
        if sigma > 0.0:
            m.latitude, m.longitude = f.noisy_gps(m.latitude, m.longitude, sigma, self.rng)
            cov = list(m.position_covariance)
            cov[0] = cov[4] = sigma * sigma
            m.position_covariance = cov
            m.position_covariance_type = NavSatFix.COVARIANCE_TYPE_DIAGONAL_KNOWN
        return m

    def fix_joints(self, m: JointState) -> JointState:
        factor = self.faults.slip_factor()
        if factor != 1.0:
            m.velocity = [
                v * factor if n.startswith("wheel_") else v
                for n, v in zip(m.name, m.velocity, strict=False)
            ]
        return m


def main() -> None:
    rclpy.init()
    node = FaultNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
