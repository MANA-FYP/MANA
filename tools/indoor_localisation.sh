#!/usr/bin/env bash
# Lidar localisation indoors, measured against Gazebo's ground truth (#43).
#
#   1. map:      the indoor world with wheel odometry (odom:=wheels), slam_toolbox mapping, one lap
#                of the corridor loop; the pose graph is saved to MAP
#   2. localise: the world again from the start, slam_toolbox on the saved map, LAPS laps
#
# Each step prints the error of the map pose (the localiser) and of wheel odometry against the
# ground truth; the drive logs are kept in OUT (mapping.csv, localisation.csv).
#
#   tools/indoor_localisation.sh                        # map, then localise for 2 laps
#   tools/indoor_localisation.sh --laps 3 --out runs/t43
#   tools/indoor_localisation.sh --no-mapping --map ~/mana_maps/indoor   # localise on a saved map
#
# Needs the workspace built and sourced, and slam_toolbox (sudo apt install ros-jazzy-slam-toolbox).
# Takes about 15 minutes for both steps. Exit 1 when a drive did not finish its route.
set -uo pipefail

MAP="$HOME/mana_maps/indoor"
OUT="indoor_localisation"
LAPS=2
MAPPING=1
while [ $# -gt 0 ]; do
  case "$1" in
    --map) MAP="$2"; shift ;;
    --out) OUT="$2"; shift ;;
    --laps) LAPS="$2"; shift ;;
    --no-mapping) MAPPING=0 ;;
    *) echo "usage: $0 [--map PATH] [--out DIR] [--laps N] [--no-mapping]" >&2; exit 2 ;;
  esac
  shift
done
mkdir -p "$OUT" "$(dirname "$MAP")"
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

run() {  # one step: the world from the start, slam_toolbox in a mode, then the drive
  local name=$1 executable=$2 config=$3 laps=$4; shift 4
  start "$OUT/$name-world.log" ros2 launch mana_sim world.launch.py \
    world:="$SHARE/worlds/mana_indoor.sdf" headless:=true odom:=wheels driver:=true
  sleep 25  # Gazebo up and the sensors publishing
  start "$OUT/$name-slam.log" ros2 run slam_toolbox "$executable" \
    --ros-args --params-file "$SHARE/config/$config" "$@"
  # slam_toolbox on Jazzy is a lifecycle node: it neither maps nor localises until activated. On a
  # fresh ROS domain it can take a while to be found, so wait for it first
  local deadline=$((SECONDS + 60))
  until timeout 5 ros2 lifecycle get /slam_toolbox >/dev/null 2>&1; do
    [ $SECONDS -ge $deadline ] && { echo "FAIL slam_toolbox was not found"; return 1; }
    sleep 2
  done
  timeout 20 ros2 lifecycle set /slam_toolbox configure >/dev/null \
    && timeout 20 ros2 lifecycle set /slam_toolbox activate >/dev/null \
    || { echo "FAIL slam_toolbox did not activate"; return 1; }
  echo "--- $name"
  ros2 run mana_sim loop_driver --laps "$laps" --csv "$OUT/$name.csv"
}

status=0
if [ "$MAPPING" = 1 ]; then
  run mapping async_slam_toolbox_node slam_mapping.yaml 1 || { echo "FAIL mapping"; exit 1; }
  rm -f "$MAP.posegraph" "$MAP.data"
  timeout 60 ros2 service call /slam_toolbox/serialize_map slam_toolbox/srv/SerializePoseGraph \
    "{filename: '$MAP'}" >/dev/null
  [ -f "$MAP.posegraph" ] && echo "map saved: $MAP.posegraph" || { echo "FAIL the map was not saved"; exit 1; }
  stop_all
fi
run localisation localization_slam_toolbox_node slam_localisation.yaml "$LAPS" \
  -p map_file_name:="$MAP" || status=1
exit $status
