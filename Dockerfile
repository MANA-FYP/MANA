# MANA on ROS 2 Jazzy: the packages in src/, built and ready to run, for example
#   docker run --rm -it ghcr.io/mana-fyp/mana:v0.1.0-mid ros2 run mana_core core_node
FROM ros:jazzy-ros-base

LABEL org.opencontainers.image.source="https://github.com/MANA-FYP/MANA" \
      org.opencontainers.image.description="MANA autonomy layer for assistive mobility devices, on ROS 2 Jazzy" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

WORKDIR /mana
COPY src src
RUN apt-get update \
 && rosdep update --rosdistro "$ROS_DISTRO" \
 && rosdep install --from-paths src --ignore-src -y \
 && . /opt/ros/"$ROS_DISTRO"/setup.sh \
 && colcon build --merge-install \
 && rm -rf build log /var/lib/apt/lists/*

ENTRYPOINT ["/bin/bash", "-c", "source /mana/install/setup.bash && exec \"$@\"", "--"]
CMD ["bash"]
