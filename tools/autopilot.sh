#!/usr/bin/env bash
# The autopilot through MANA, in Gazebo (#78).
#
# The indoor world with a driver and wheel odometry, the core, slam_toolbox localising on a saved
# map, the lidar gate and ekf_global (#43), then Nav2 (launch/autopilot.launch.py), whose commands
# go through driver_select and the host, capped by MANA. tools/autopilot_goal.py sends the goals
# and writes down how each went.
#
#   tools/autopilot.sh --map ~/mana_maps/indoor --out runs/t78
#
# Needs the workspace built and sourced, slam_toolbox, robot_localization and Nav2
# (ros-jazzy-navigation2). Exit 1 when a goal is not reached.
set -uo pipefail

MAP="$HOME/mana_maps/indoor"
OUT="autopilot"
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
EKF="$(ros2 pkg prefix mana_position)/share/mana_position/config/localisation.yaml"
HERE="$(cd "$(dirname "$0")" && pwd)"

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

start "$OUT/world.log" ros2 launch mana_sim world.launch.py \
  world:="$SHARE/worlds/mana_indoor.sdf" headless:=true odom:=wheels driver:=true
sleep 25  # Gazebo up and the sensors publishing
start "$OUT/core.log" ros2 run mana_core core_node --ros-args --params-file "$SHARE/config/core.yaml"
start "$OUT/slam.log" ros2 run slam_toolbox localization_slam_toolbox_node \
  --ros-args --params-file "$SHARE/config/slam_localisation.yaml" -p map_file_name:="$MAP" \
  -p transform_publish_period:=0.0
# the ros2 command line itself can take more than 5 s to start on a busy computer
deadline=$((SECONDS + 120))
until timeout 20 ros2 lifecycle get /slam_toolbox >/dev/null 2>&1; do
  [ $SECONDS -ge $deadline ] && { echo "FAIL slam_toolbox was not found"; exit 1; }
  sleep 2
done
timeout 40 ros2 lifecycle set /slam_toolbox configure >/dev/null \
  && timeout 40 ros2 lifecycle set /slam_toolbox activate >/dev/null \
  || { echo "FAIL slam_toolbox did not activate"; exit 1; }
start "$OUT/gate.log" ros2 run mana_position lidar_gate --ros-args -p use_sim_time:=true \
  -r pose:=/pose -r odom:=/odom
# ekf_global owns map -> odom (slam_toolbox publishes none, transform_publish_period 0)
start "$OUT/ekf.log" ros2 run robot_localization ekf_node --ros-args -r __node:=ekf_global \
  --params-file "$EKF" -p use_sim_time:=true -r wheel/odometry:=/odom -r imu/data:=/imu \
  -r odometry/filtered:=/odometry/global -r lidar/pose:=/lidar/pose
start "$OUT/nav2.log" ros2 launch mana_sim autopilot.launch.py
start "$OUT/drive_request.csv" ros2 topic echo /drive_request mana_msgs/msg/DriveRequest --csv
python3 "$HERE/autopilot_goal.py" --out "$OUT/goals.csv"
