#!/usr/bin/env bash
# Check a MANA development machine: one PASS, FAIL or SKIP line per item.
# Run it from the repository root. It exits non-zero if anything fails.
set -uo pipefail

fail=0
check() {  # check <label> <command...>: run the command and show its last line of output
  local label=$1 out
  shift
  if out=$("$@" 2>&1); then
    printf 'PASS  %-12s %s\n' "$label" "$(tail -n 1 <<<"$out")"
  else
    printf 'FAIL  %-12s %s\n' "$label" "$(tail -n 1 <<<"$out")"
    fail=1
  fi
}

if [ -f /opt/ros/jazzy/setup.bash ]; then
  set +u  # ROS setup files are not written for set -u
  source /opt/ros/jazzy/setup.bash
  set -u
fi

check "Ubuntu" bash -c '. /etc/os-release; [ "$VERSION_ID" = 24.04 ] && echo "$PRETTY_NAME"'
check "ROS 2" bash -c '[ "${ROS_DISTRO:-}" = jazzy ] && echo "ROS_DISTRO=$ROS_DISTRO"'
check "ros2 CLI" ros2 pkg prefix rclpy
check "Gazebo" bash -c 'gz sim --version | head -n 1'
check "colcon" bash -c 'command -v colcon || { echo "colcon not found"; exit 1; }'
check "rosdep" rosdep --version
# build the workspace outside the repo, so the check leaves no build files behind
tmp=$(mktemp -d)
check "Workspace" colcon --log-base "$tmp/log" build --base-paths src \
  --build-base "$tmp/build" --install-base "$tmp/install"
rm -rf "$tmp"

ML="$HOME/.venvs/mana-ml/bin/python"
if [ -x "$ML" ]; then
  check "ML venv" "$ML" -c "import torch, torchvision, ultralytics, onnx, onnxruntime
print('torch', torch.__version__, '| ultralytics', ultralytics.__version__, '| onnxruntime', onnxruntime.__version__)"
else
  printf 'SKIP  %-12s %s\n' "ML venv" "not set up (tools/setup_ubuntu.sh --ml, for the model work)"
fi

if [ "$fail" = 0 ]; then echo "All checks passed."; else echo "Some checks failed."; fi
exit "$fail"
