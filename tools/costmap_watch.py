"""Record a costmap and the chair's footprint through a run, to see what Nav2 stopped for (#78).

    python3 tools/costmap_watch.py --seconds 400 --out out/costmaps.npz &

Keeps every costmap message (the local costmap by default, small and about once a second) with its
time stamp, origin and resolution, and every footprint polygon, in one .npz. Set it beside the
controller's "collision ahead" times in nav2.log to see which cells the footprint met.
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import rclpy
from geometry_msgs.msg import PolygonStamped
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node


def stamp_of(header) -> float:
    return header.stamp.sec + header.stamp.nanosec * 1e-9


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--costmap", default="/local_costmap/costmap")
    p.add_argument("--footprint", default="/local_costmap/published_footprint")
    p.add_argument("--seconds", type=float, default=300.0, help="wall-clock time to record")
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    rclpy.init()
    node = Node("costmap_watch")
    grids, meta, prints = [], [], []

    def on_grid(m: OccupancyGrid) -> None:
        i = m.info
        grids.append(np.array(m.data, dtype=np.int8).reshape(i.height, i.width))
        meta.append((stamp_of(m.header), i.origin.position.x, i.origin.position.y, i.resolution))

    def on_print(m: PolygonStamped) -> None:
        prints.append([stamp_of(m.header)] + [v for pt in m.polygon.points for v in (pt.x, pt.y)])

    node.create_subscription(OccupancyGrid, a.costmap, on_grid, 10)
    node.create_subscription(PolygonStamped, a.footprint, on_print, 10)
    end = time.monotonic() + a.seconds
    while time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.1)
    node.destroy_node()
    rclpy.shutdown()
    shapes = {g.shape for g in grids}
    np.savez_compressed(
        a.out,
        grids=np.stack(grids) if len(shapes) == 1 else np.array(grids, dtype=object),
        meta=np.array(meta),  # stamp, origin x, origin y, resolution
        footprints=np.array(prints),  # stamp, then x, y of each corner
    )
    print(f"{len(grids)} costmaps and {len(prints)} footprints in {a.out}")
    return 0 if grids else 1


if __name__ == "__main__":
    raise SystemExit(main())
