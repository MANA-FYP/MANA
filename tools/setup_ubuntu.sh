#!/usr/bin/env bash
# Set up a MANA development machine on Ubuntu 24.04 (native or WSL 2).
# Installs ROS 2 Jazzy (desktop), Gazebo Harmonic through ros_gz, the ROS dev tools and rosdep.
# With --ml it also makes a Python venv for the model work, with the CPU build of PyTorch.
# Safe to run again. Check the result with tools/check_env.sh.
set -euo pipefail

ML=0
for arg in "$@"; do
  case "$arg" in
    --ml) ML=1 ;;
    *) echo "usage: $0 [--ml]" >&2; exit 2 ;;
  esac
done

. /etc/os-release
if [ "$VERSION_ID" != "24.04" ]; then
  echo "ROS 2 Jazzy needs Ubuntu 24.04; this machine runs $PRETTY_NAME." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update
sudo apt-get install -y curl software-properties-common
sudo add-apt-repository -y universe

# ROS 2 apt source, the method in the Jazzy install guide; pinned so every machine gets the same
ROS_APT_SOURCE_VERSION=1.3.0
if ! dpkg -s ros2-apt-source >/dev/null 2>&1; then
  curl -fsSL -o /tmp/ros2-apt-source.deb \
    "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.${UBUNTU_CODENAME:-$VERSION_CODENAME}_all.deb"
  sudo dpkg -i /tmp/ros2-apt-source.deb
  sudo apt-get update
fi

sudo apt-get install -y ros-jazzy-desktop ros-jazzy-ros-gz ros-dev-tools python3-venv

if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then
  sudo rosdep init
fi
rosdep update --rosdistro jazzy

if [ "$ML" = 1 ]; then
  VENV="$HOME/.venvs/mana-ml"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip
  "$VENV/bin/pip" install --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple \
    -r "$(dirname "$0")/../requirements-ml.txt"
fi

echo "Setup finished. Open a new terminal or run: source /opt/ros/jazzy/setup.bash"
