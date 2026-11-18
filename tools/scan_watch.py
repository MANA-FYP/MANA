"""Watch a lidar scan for a while and write the nearest return ahead in each message (#79).

    python3 tools/scan_watch.py --topic /scan_raw --seconds 60 --out out/scan.csv
    python3 tools/scan_watch.py --out out/scan.csv --frames out/frames.npz   # and camera frames

Each row is the message's time stamp and the nearest valid range within --sector degrees either
side of straight ahead. It prints how many messages came and how many saw something nearer than
--near metres, so a run with people walking across in front can be set beside one without. With
--frames, a colour camera frame is kept every --every seconds too (RGB, in an .npz), to run a
detector on afterwards.
"""

from __future__ import annotations

import argparse
import csv
import math
import time

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, LaserScan


def nearest_ahead(m: LaserScan, sector_deg: float) -> float:
    """The nearest valid range within sector_deg of straight ahead, inf when there is none."""
    half = math.radians(sector_deg)
    best = math.inf
    for i, r in enumerate(m.ranges):
        a = m.angle_min + i * m.angle_increment
        if abs(a) <= half and m.range_min <= r <= m.range_max:
            best = min(best, r)
    return best


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--topic", default="/scan_raw")
    p.add_argument("--seconds", type=float, default=60.0, help="wall-clock time to watch")
    p.add_argument("--sector", type=float, default=30.0, help="degrees either side of ahead")
    p.add_argument("--near", type=float, default=4.0, help="metres: a return this close counts")
    p.add_argument("--out", required=True)
    p.add_argument("--frames", help="an .npz for camera frames (none when not given)")
    p.add_argument("--camera", default="/camera/image_raw")
    p.add_argument("--every", type=float, default=2.0, help="seconds between kept frames")
    a = p.parse_args(argv)
    rclpy.init()
    node = Node("scan_watch")
    rows = []

    def on_scan(m: LaserScan) -> None:
        stamp = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
        rows.append((round(stamp, 3), nearest_ahead(m, a.sector)))

    node.create_subscription(LaserScan, a.topic, on_scan, qos_profile_sensor_data)
    frames, stamps, last = [], [], [-math.inf]

    def on_image(m: Image) -> None:
        if time.monotonic() - last[0] < a.every or m.encoding not in ("rgb8", "bgr8"):
            return
        last[0] = time.monotonic()
        rgb = np.frombuffer(bytes(m.data), np.uint8).reshape(m.height, m.step)[:, : m.width * 3]
        rgb = rgb.reshape(m.height, m.width, 3)
        frames.append(rgb if m.encoding == "rgb8" else rgb[:, :, ::-1])
        stamps.append(m.header.stamp.sec + m.header.stamp.nanosec * 1e-9)

    if a.frames:
        node.create_subscription(Image, a.camera, on_image, qos_profile_sensor_data)
    end = time.monotonic() + a.seconds
    while time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.1)
    node.destroy_node()
    rclpy.shutdown()
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["t", "nearest_ahead_m"])
        w.writerows(rows)
    if a.frames and frames:
        np.savez_compressed(a.frames, frames=np.stack(frames), stamps=np.array(stamps))
    near = sum(r < a.near for _, r in rows)
    finite = [r for _, r in rows if math.isfinite(r)]
    nearest = f"nearest {min(finite):.2f} m" if finite else "nothing ahead"
    print(f"{len(rows)} scans on {a.topic}; {near} within {a.near:g} m ahead; {nearest}")
    if a.frames:
        print(f"{len(frames)} camera frames kept in {a.frames}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
