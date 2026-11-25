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
#   tools/indoor_localisation.sh --no-mapping --ekf --out runs/t43-ekf   # the EKF with the gated lidar
#   tools/indoor_localisation.sh --world campus --map ~/mana_maps/campus   # the campus building (#79)
#
# --world campus: the campus (mana_sim.campus) from its lobby, and the loop round the building's
# corridor and hall (mana_sim.localisation.ROUTES), all on its floor, for the autopilot's map.
#
# --ekf: in the localisation step slam_toolbox's map pose goes through mana_position's lidar_gate
# (jumps against the wheels' move taken out) into ekf_global (with the wheels and the IMU), and the
# map pose measured is the EKF's. One publisher per transform: slam_toolbox's map > odom is off
# (transform_publish_period 0), ekf_global gives map > odom, and the wheels' dead reckoning in the
# world (odom:=wheels) stays the only odom > base_link, so ekf_local is not started.
#
# Needs the workspace built and sourced, and slam_toolbox (sudo apt install ros-jazzy-slam-toolbox).
# Takes about 15 minutes for both steps. Exit 1 when a drive did not finish its route.
set -uo pipefail

MAP="$HOME/mana_maps/indoor"
OUT="indoor_localisation"
LAPS=2
MAPPING=1
EKF=0
WORLD=indoor
while [ $# -gt 0 ]; do
  case "$1" in
    --map) MAP="$2"; shift ;;
    --out) OUT="$2"; shift ;;
    --laps) LAPS="$2"; shift ;;
    --no-mapping) MAPPING=0 ;;
    --ekf) EKF=1 ;;
    --world) WORLD="$2"; shift ;;
    *) echo "usage: $0 [--map PATH] [--out DIR] [--laps N] [--no-mapping] [--ekf] [--world indoor|campus]" >&2; exit 2 ;;
  esac
  shift
done
mkdir -p "$OUT" "$(dirname "$MAP")"
SHARE="$(ros2 pkg prefix mana_sim)/share/mana_sim"
POSITION="$(ros2 pkg prefix mana_position)/share/mana_position"
WORLD_FILE="$SHARE/worlds/mana_indoor.sdf"
if [ "$WORLD" = campus ]; then
  WORLD_FILE="$OUT/campus_lobby.sdf"
  python3 -m mana_sim.campus --start lobby > "$WORLD_FILE" || exit 2
fi

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
    world:="$WORLD_FILE" headless:=true odom:=wheels driver:=true
  sleep 25  # Gazebo up and the sensors publishing
  start "$OUT/$name-slam.log" ros2 run slam_toolbox "$executable" \
    --ros-args --params-file "$SHARE/config/$config" "$@"
  # slam_toolbox on Jazzy is a lifecycle node: it neither maps nor localises until activated. On a
  # fresh ROS domain it can take a while to be found, and the ros2 command line itself can take
  # more than 5 s to start on a busy computer (as in tools/autopilot.sh), so wait for it first
  local deadline=$((SECONDS + 120))
  until timeout 20 ros2 lifecycle get /slam_toolbox >/dev/null 2>&1; do
    [ $SECONDS -ge $deadline ] && { echo "FAIL slam_toolbox was not found"; return 1; }
    sleep 2
  done
  timeout 40 ros2 lifecycle set /slam_toolbox configure >/dev/null \
    && timeout 40 ros2 lifecycle set /slam_toolbox activate >/dev/null \
    || { echo "FAIL slam_toolbox did not activate"; return 1; }
  if [ "$name" = localisation ] && [ "$EKF" = 1 ]; then
    start "$OUT/$name-gate.log" ros2 run mana_position lidar_gate --ros-args \
      -p use_sim_time:=true -r pose:=/pose -r odom:=/odom
    start "$OUT/$name-ekf.log" ros2 run robot_localization ekf_node --ros-args \
      -r __node:=ekf_global --params-file "$POSITION/config/localisation.yaml" -p use_sim_time:=true \
      -r wheel/odometry:=/odom -r imu/data:=/imu -r odometry/filtered:=/odometry/global \
      -r lidar/pose:=/lidar/pose
    # the drive's first row waits for map > odom itself (loop_driver), so nothing is polled here
  fi
  echo "--- $name"
  ros2 run mana_sim loop_driver --laps "$laps" --csv "$OUT/$name.csv" --route "$WORLD"
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
SLAM_TF=()  # with the EKF, map > odom is ekf_global's alone
[ "$EKF" = 1 ] && SLAM_TF=(-p transform_publish_period:=0.0)
run localisation localization_slam_toolbox_node slam_localisation.yaml "$LAPS" \
  -p map_file_name:="$MAP" "${SLAM_TF[@]}" || status=1
exit $status
