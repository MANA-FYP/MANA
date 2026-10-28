#!/usr/bin/env bash
# Check that every simulated sensor publishes at the rate in src/mana_sim/docs/topics.md.
# Start the simulation first (ros2 launch mana_sim world.launch.py headless:=true), then run this
# in another terminal. Rates are in simulation seconds, so a slow Gazebo does not fail the check.
# Prints PASS or FAIL per topic and exits 1 if any fails.
set -uo pipefail

# topic  documented rate (Hz)
TOPICS="camera/image:15 depth/image:10 scan:10 imu:100 gps/fix:5 joint_states:250 odom:20"
TOLERANCE=0.15  # 15 percent

failed=0
for entry in $TOPICS; do
  topic=${entry%%:*}
  want=${entry##*:}
  got=$(timeout 20 ros2 topic hz "$topic" --use-sim-time --window 30 2>/dev/null |
    awk '/average rate/ {r=$3} END {print r}')
  if [ -z "$got" ]; then
    echo "FAIL $topic: nothing received"
    failed=1
    continue
  fi
  if awk -v g="$got" -v w="$want" -v t="$TOLERANCE" 'BEGIN {exit !(g >= w*(1-t) && g <= w*(1+t))}'; then
    echo "PASS $topic: $got Hz (want $want)"
  else
    echo "FAIL $topic: $got Hz (want $want)"
    failed=1
  fi
done
exit $failed
