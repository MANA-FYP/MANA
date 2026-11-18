"""Wait until a transform is known, then exit 0; exit 1 after --seconds (#78).

    python3 tools/wait_tf.py map base_link --seconds 120
    python3 tools/wait_tf.py map base_link --min-x 0.5 --seconds 10   # and x >= 0.5 m

For scripts that must not start a node before its frames exist (Nav2's costmaps give up when
map -> odom -> base_link is missing as they start). It asks a TF buffer directly, rather than
reading tf2_echo's printout, which is buffered when piped.
"""

from __future__ import annotations

import argparse
import time

import rclpy
from rclpy.node import Node
from rclpy.time import Time
from tf2_ros import Buffer, TransformListener


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("target")
    p.add_argument("source")
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--min-x", type=float, help="also wait for x >= this")
    a = p.parse_args(argv)
    rclpy.init()
    node = Node("wait_tf")
    buffer = Buffer()
    TransformListener(buffer, node)
    end = time.monotonic() + a.seconds
    found = False
    while time.monotonic() < end and not found:
        rclpy.spin_once(node, timeout_sec=0.2)
        found = buffer.can_transform(a.target, a.source, Time())
        if found and a.min_x is not None:
            x = buffer.lookup_transform(a.target, a.source, Time()).transform.translation.x
            found = x >= a.min_x
    node.destroy_node()
    rclpy.shutdown()
    print(f"{a.target} -> {a.source}: {'found' if found else 'not found'}")
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
