"""Save the simulated 3D camera's frames in front of each hazard of the Gazebo route (#39), for
tests/test_gazebo_depth_frames.py. Run it beside the Gazebo runs, in the same ROS domain:

    python3 tools/grab_depth_frames.py tests/fixtures/gazebo_depth.npz &
    python3 -m mana_core.evaluate --world gazebo stairs curb drop --traces /tmp/traces
    touch /tmp/grab_depth_frames.stop      # it writes the file and ends

A frame is kept the first time the camera is 2.5, 2.0, 1.5 or 1.0 m (within 5 cm) from the stairs,
the curb or the drop, and once on the flat between the stairs and the curb, with the true distance
and the chair's pitch from Gazebo's ground truth. On the stairs, the chair pitched, it keeps one
frame for each 0.1 rad of pitch: the one nearest that pitch. Depth is kept in millimetres (16UC1).
"""

import math
import sys
import time
from pathlib import Path

import numpy as np
import rclpy
from mana_sim.frames import SENSORS
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image

from mana_perception import depth as dp

HAZARDS = ((22.0, "stairs_up"), (31.0, "curb"), (38.0, "drop"))  # worlds/mana_route.sdf
FLAT = (26.5, 27.5)  # the chair here: the camera sees only flat ground (the stairs end at 25.4 m)
TARGETS = (2.5, 2.0, 1.5, 1.0)
STAIRS = (20.5, 25.4)  # the chair here, pitched: climbing
CAM_X, _, CAM_Z = SENSORS["depth_link"][:3]
STOP = Path("/tmp/grab_depth_frames.stop")


class Grab(Node):
    def __init__(self):
        super().__init__("grab_depth_frames")
        self.k = self.pose = None
        self.saved = {}
        sensor = qos_profile_sensor_data
        self.create_subscription(Image, "depth/image_raw", self.on_image, sensor)
        self.create_subscription(CameraInfo, "depth/camera_info", self.on_info, sensor)
        self.create_subscription(Odometry, "/model/mana_chair/ground_truth", self.on_odom, 10)

    def on_info(self, m):
        self.k = (m.k[0], m.k[4], m.k[2], m.k[5])

    def on_odom(self, m):
        p, q = m.pose.pose.position, m.pose.pose.orientation
        self.pose = (p.x, math.asin(max(-1, min(1, 2 * (q.w * q.y - q.z * q.x)))))

    def on_image(self, m):
        if self.pose is None or self.k is None:
            return
        x, pitch = self.pose
        cam_x = x + CAM_X * math.cos(pitch) + CAM_Z * math.sin(pitch)
        ahead = [(at - cam_x, name) for at, name in HAZARDS if at > cam_x]
        keys = []
        if ahead:
            gap, name = min(ahead)
            keys = [(f"{name}:{t}", gap) for t in TARGETS if abs(gap - t) < 0.05]
        if FLAT[0] < x < FLAT[1] and abs(pitch) < 0.01:
            keys.append(("flat:27", math.nan))
        band = round(pitch, 1)
        climbing = STAIRS[0] < x < STAIRS[1] and abs(band) >= 0.1
        if climbing:
            old = self.saved.get(f"climb:{band:+.1f}")
            if old is None or abs(pitch - band) < abs(old[2] - band):
                self.saved.pop(f"climb:{band:+.1f}", None)  # a nearer one replaces it
                keys.append((f"climb:{band:+.1f}", math.nan))
        for key, gap in keys:
            if key not in self.saved:
                depth = dp.to_metres(m.encoding, m.height, m.width, m.step, m.data, m.is_bigendian)
                if depth is None:
                    return
                mm = np.where(np.isfinite(depth), np.clip(depth * 1000, 0, 65535), 0)
                self.saved[key] = (mm.astype(np.uint16), gap, pitch)
                if not climbing:
                    print(f"kept {key}: {gap:.3f} m, pitch {pitch:.4f} rad", flush=True)


def main():
    out = Path(sys.argv[1])
    STOP.unlink(missing_ok=True)
    rclpy.init()
    node = Grab()
    while not STOP.exists():
        rclpy.spin_once(node, timeout_sec=0.1)
    frames = {"k": np.array(node.k)}
    for i, (key, (mm, gap, pitch)) in enumerate(sorted(node.saved.items())):
        frames |= {f"f{i}_name": np.array(key), f"f{i}_depth_mm": mm}
        frames[f"f{i}_meta"] = np.array([gap, pitch])
    np.savez_compressed(out, **frames)
    for key, (_, _, pitch) in sorted(node.saved.items()):
        print(f"{key}: pitch {pitch:.4f} rad")
    print(f"{len(node.saved)} frames in {out}")
    time.sleep(0.1)


if __name__ == "__main__":
    main()
