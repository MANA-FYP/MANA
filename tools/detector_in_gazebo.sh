#!/bin/bash
# The object detector on Gazebo's RGB camera (#40): a standing person (Gazebo Fuel, "Standing
# person" by OpenRobotics, CC0) D metres ahead of the camera on the level start of the route, the
# chair standing still. For each distance it launches the world with detector:=true, waits for it
# to settle, and prints how many ObstacleReports the detector sent in 20 s and their distances.
#
#   source install/setup.bash        # a built workspace, with ultralytics importable
#   tools/detector_in_gazebo.sh ~/yolo26n.pt 2 3 5 7
#
# The person model is fetched from Gazebo Fuel on the first run (needs the internet once).
set -u
WEIGHTS=${1:?weights, e.g. yolo26n.pt}
shift
SRC=$(ros2 pkg prefix mana_sim)/share/mana_sim/worlds/mana_route.sdf
PERSON="https://fuel.gazebosim.org/1.0/OpenRobotics/models/Standing person"
for D in "$@"; do
  if pgrep -f "gz sim" >/dev/null; then echo "another gz sim runs"; exit 1; fi
  X=$(python3 -c "print(-1 + 0.35 + $D)")  # the chair at -1 m, its camera 0.35 m ahead
  WORLD=$(mktemp --suffix .sdf)
  sed "s#<pose>1 0 0.38 0 0 0</pose>#<pose>-1 0 0.38 0 0 0</pose>#" "$SRC" |
    sed "s#  </world>#    <include><uri>$PERSON</uri><name>person</name><pose>$X 0 0 0 0 3.1416</pose></include>\n  </world>#" >"$WORLD"
  setsid ros2 launch mana_sim world.launch.py world:="$WORLD" headless:=true detector:=true \
    weights:="$WEIGHTS" >"$WORLD.log" 2>&1 &
  LAUNCH=$!
  sleep 40  # Gazebo up, the detector's model loaded
  RATE=$(timeout 12 ros2 topic hz /camera/image 2>/dev/null | awk '/average rate/ {r=$3} END {print r ? r : "?"}')
  echo -n "person $D m from the camera ($RATE camera frames/s): "
  python3 - <<'EOF'
import statistics, time
import rclpy
from rclpy.node import Node
from mana_msgs.msg import ObstacleReport

class Listen(Node):
    def __init__(self):
        super().__init__("detector_in_gazebo")
        self.seen = []
        self.create_subscription(ObstacleReport, "/obstacle", self.on, 50)

    def on(self, m):
        if m.source == ObstacleReport.DETECTOR:
            self.seen.append((m.distance_m, m.trust, m.label))

rclpy.init()
node = Listen()
end = time.time() + 20
while time.time() < end:
    rclpy.spin_once(node, timeout_sec=0.1)
d = [s[0] for s in node.seen]
if d:
    labels = sorted({s[2] for s in node.seen})
    print(f"{len(d)} reports, distance median {statistics.median(d):.2f} m"
          f" ({min(d):.2f} to {max(d):.2f}), trust {statistics.median(s[1] for s in node.seen):.2f},"
          f" {', '.join(labels)}")
else:
    print("no report")
EOF
  kill -TERM -- -$LAUNCH  # the launch and its nodes (a script's background job ignores SIGINT)
  wait $LAUNCH 2>/dev/null
  pkill -f "gz sim"
  sleep 3
  if pgrep -f "gz sim" >/dev/null; then echo "gz sim did not stop"; exit 1; fi
  rm -f "$WORLD" "$WORLD.log"
  sleep 2
done
