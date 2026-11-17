#!/usr/bin/env bash
# GPS to lidar map switching, tested in Gazebo with the GPS-loss fault (#42).
#
# The indoor world with GPS added (the NavSat system and a place on the earth, as in
# mana_route.sdf), wheel odometry, slam_toolbox localising on a saved map (tools/indoor_localisation.sh
# makes one) and the lidar gate. The chair drives one lap of the corridor loop while fault_script
# drops the GPS twice for 30 s (gps_two_outages). The position node's reports are logged and scored
# with mana_sim.switching: the source must leave GPS within 2 s of each loss, and change at most
# twice per outage (away and back).
#
#   tools/gps_lidar_switch.sh --map ~/mana_maps/indoor --out runs/t42
#
# Needs the workspace built and sourced, and slam_toolbox. Exit 1 when the switching fails.
set -uo pipefail

MAP="$HOME/mana_maps/indoor"
OUT="gps_lidar_switch"
while [ $# -gt 0 ]; do
  case "$1" in
    --map) MAP="$2"; shift ;;
    --out) OUT="$2"; shift ;;
    *) echo "usage: $0 [--map PATH] [--out DIR]" >&2; exit 2 ;;
  esac
  shift
done
[ -f "$MAP.posegraph" ] || { echo "no map at $MAP: make one with tools/indoor_localisation.sh"; exit 2; }
mkdir -p "$OUT"
SHARE="$(ros2 pkg prefix mana_sim)/share/mana_sim"

PIDS=()
stop_all() {  # stop what this script started (each in its own process group): ask, wait, kill
  for pid in "${PIDS[@]}"; do kill -TERM -- "-$pid" 2>/dev/null; done
  for _ in 1 2 3 4 5; do
    alive=0
    for pid in "${PIDS[@]}"; do kill -0 -- "-$pid" 2>/dev/null && alive=1; done
    [ $alive = 0 ] && break
    sleep 1
  done
  for pid in "${PIDS[@]}"; do kill -KILL -- "-$pid" 2>/dev/null; done
  wait 2>/dev/null
  PIDS=()
}
trap stop_all EXIT

start() {  # start a command in its own process group, output to a file
  local out=$1; shift
  setsid "$@" >"$out" 2>&1 &
  PIDS+=($!)
}

WORLD="$OUT/indoor_gps.sdf"
python3 - "$SHARE/worlds/mana_indoor.sdf" "$WORLD" <<'PY'
import sys

src, dst = sys.argv[1:]
s = open(src).read()
imu = '<plugin filename="gz-sim-imu-system" name="gz::sim::systems::Imu"/>'
if s.count(imu) != 1:
    sys.exit("expected one IMU system in the indoor world")
gps = (
    '\n    <plugin filename="gz-sim-navsat-system" name="gz::sim::systems::NavSat"/>'
    "\n    <spherical_coordinates><surface_model>EARTH_WGS84</surface_model>"
    "<latitude_deg>33.6844</latitude_deg><longitude_deg>73.0479</longitude_deg>"
    "<elevation>540</elevation><heading_deg>0</heading_deg></spherical_coordinates>"
)
open(dst, "w").write(s.replace(imu, imu + gps))
PY

start "$OUT/world.log" ros2 launch mana_sim world.launch.py \
  world:="$WORLD" headless:=true odom:=wheels driver:=true
sleep 25  # Gazebo up and the sensors publishing
start "$OUT/slam.log" ros2 run slam_toolbox localization_slam_toolbox_node \
  --ros-args --params-file "$SHARE/config/slam_localisation.yaml" -p map_file_name:="$MAP"
deadline=$((SECONDS + 60))
until timeout 5 ros2 lifecycle get /slam_toolbox >/dev/null 2>&1; do
  [ $SECONDS -ge $deadline ] && { echo "FAIL slam_toolbox was not found"; exit 1; }
  sleep 2
done
timeout 20 ros2 lifecycle set /slam_toolbox configure >/dev/null \
  && timeout 20 ros2 lifecycle set /slam_toolbox activate >/dev/null \
  || { echo "FAIL slam_toolbox did not activate"; exit 1; }
start "$OUT/gate.log" ros2 run mana_position lidar_gate --ros-args -p use_sim_time:=true \
  -r pose:=/pose -r odom:=/odom
start "$OUT/position.csv" ros2 topic echo /position mana_msgs/msg/PositionReport --csv
start "$OUT/fix.csv" ros2 topic echo /gps/fix sensor_msgs/msg/NavSatFix --csv  # when each fix came, and how good
start "$OUT/faults.log" ros2 run mana_sim fault_script gps_two_outages --ros-args -p use_sim_time:=true
ros2 run mana_sim loop_driver --laps 1 --csv "$OUT/drive.csv"
python3 -m mana_sim.switching "$OUT/position.csv" "$OUT/faults.log"
