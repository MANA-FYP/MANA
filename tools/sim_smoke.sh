#!/usr/bin/env bash
# Smoke test of the simulation: Gazebo starts headless, the core is started, and the core answers.
# Passes when a DriveRequest and a UserMessage are published within the time limit.
#
#   tools/sim_smoke.sh               # start the world and the core, wait for both topics
#   tools/sim_smoke.sh --no-core     # without the core: must fail (CI checks that it does)
#   tools/sim_smoke.sh --timeout 120 # seconds to wait (default 90)
#
# Needs the workspace built and sourced (source install/setup.bash). Runs with no GPU: the world has
# no cameras and no lidar (sensors:=false). Exit 0 on success, 1 when a topic did not appear.
set -uo pipefail

CORE=1
TIMEOUT=90
while [ $# -gt 0 ]; do
  case "$1" in
    --no-core) CORE=0 ;;
    --timeout) TIMEOUT="$2"; shift ;;
    *) echo "usage: $0 [--no-core] [--timeout SECONDS]" >&2; exit 2 ;;
  esac
  shift
done

LOGS=$(mktemp -d)
PIDS=()
cleanup() {
  for pid in "${PIDS[@]}"; do kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null; done
  pkill -f "gz sim" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup EXIT

start() {  # start a command in its own process group, output to a file
  local out=$1; shift
  setsid "$@" >"$out" 2>&1 &
  PIDS+=($!)
}
listen() {  # wait for one message on a topic
  local out=$1; shift
  setsid "$@" >"$out" 2>&1 &
  PIDS+=($!)
}

# listen first: both messages can come in the first second of the core. The message types are given,
# or echo gives up at once when the topic does not exist yet.
listen "$LOGS/drive_request" ros2 topic echo --once /drive_request mana_msgs/msg/DriveRequest
listen "$LOGS/user_message" ros2 topic echo --once /user_message mana_msgs/msg/UserMessage
start "$LOGS/launch.log" ros2 launch mana_sim world.launch.py headless:=true sensors:=false
if [ "$CORE" = 1 ]; then
  start "$LOGS/core.log" ros2 run mana_core core_node --ros-args -p use_sim_time:=true
fi

deadline=$((SECONDS + TIMEOUT))
heard() { grep -q "^---" "$LOGS/$1"; }  # a message ends with ---; a silent topic only warns
until heard drive_request && heard user_message; do
  if [ $SECONDS -ge $deadline ]; then break; fi
  sleep 1
done

status=0
for topic in drive_request user_message; do
  if heard "$topic"; then
    echo "PASS $topic was published"
  else
    echo "FAIL $topic was not published within ${TIMEOUT} s"
    status=1
  fi
done
if [ $status -ne 0 ]; then
  echo "--- launch log (tail)"; tail -n 25 "$LOGS/launch.log"
  [ "$CORE" = 1 ] && { echo "--- core log (tail)"; tail -n 10 "$LOGS/core.log"; }
fi
exit $status
