FROM ros:jazzy-ros-base

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /ws

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-colcon-common-extensions python3-rosdep \
    && rm -rf /var/lib/apt/lists/*

COPY src ./src

RUN . /opt/ros/jazzy/setup.sh \
    && if [ -n "$(find src -name package.xml -print -quit)" ]; then \
         (rosdep init || true) && rosdep update \
         && rosdep install --from-paths src --ignore-src -y \
         && colcon build; \
       else \
         echo "No ROS 2 packages in src/ yet"; \
       fi

CMD ["bash", "-c", "source /opt/ros/jazzy/setup.bash && ros2 --help"]
