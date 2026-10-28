"""What the simulated sensors say, turned into what the core reads. Pure Python, no ROS: the node in
sensors_node.py only moves the numbers in and out of messages.
"""

import math
import re

WHEEL_RADIUS_M = 0.15
TRACK_M = 0.7  # between the left and the right wheels
WHEEL_JOINT = re.compile(r"wheel_[fr][lr]_joint")


def tilt(x: float, y: float, z: float, w: float) -> tuple[float, float]:
    """(pitch, roll) in radians from an orientation quaternion. REP 103: pitch > 0 is nose down."""
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    return pitch, roll


def wheel_sides(names: list[str], velocities: list[float]) -> tuple[list[float], list[float]]:
    """The speeds in rad/s of the left and of the right wheels from a joint state. Only the four
    wheel joints (wheel_fl_joint ...) count: the slides and anything else are left out."""
    left, right = [], []
    for name, v in zip(names, velocities, strict=False):
        if WHEEL_JOINT.fullmatch(name):
            (left if name[7] == "l" else right).append(v)
    return left, right


def wheel_slip(
    wheel_rad_s: list[float],
    ground_mps: float,
    limit_mps: float = 0.3,
    min_mps: float = 0.2,
) -> bool:
    """The wheels turn much faster (or slower) than the chair moves: they slip. The mean of the
    wheel speeds is signed, so on a turn on the spot, when the two sides turn opposite ways, it
    cancels and is not read as slip (turn_slip looks at the turn). Below min_mps there is too little
    movement to tell. Wheels that turn forward while the chair slides back are not seen: only the
    size of the ground speed is compared."""
    if not wheel_rad_s:
        return False
    wheels_mps = abs(sum(wheel_rad_s) / len(wheel_rad_s)) * WHEEL_RADIUS_M
    if max(wheels_mps, abs(ground_mps)) < min_mps:
        return False
    return abs(wheels_mps - abs(ground_mps)) > limit_mps


def turn_slip(
    left_rad_s: list[float], right_rad_s: list[float], gyro_rad_s: float, limit_rad_s: float = 0.6
) -> bool:
    """The wheels say the chair turns much faster than its gyro says: they slip in the turn. The
    wheels give a yaw rate of (right - left) * radius / track. A skid steer chair turns less than
    the wheels say (about 0.67 of it, in the simulation), so the limit leaves room for that: at the
    1 rad/s the host allows, a normal turn differs by 0.33 rad/s, a slip of twice by 1.3."""
    if not left_rad_s or not right_rad_s:
        return False
    left = sum(left_rad_s) / len(left_rad_s)
    right = sum(right_rad_s) / len(right_rad_s)
    wheels_yaw = (right - left) * WHEEL_RADIUS_M / TRACK_M
    return abs(wheels_yaw - gyro_rad_s) > limit_rad_s


def nearest_ahead(
    ranges: list[float],
    angle_min: float,
    angle_increment: float,
    cone_rad: float,
    max_m: float,
    min_m: float = 0.0,
) -> tuple[float, float] | None:
    """(distance, bearing) of the nearest echo within cone_rad of straight ahead, or None. Bearing
    is positive to the left. Echoes that are not finite, closer than min_m or farther than max_m are
    ignored."""
    best = None
    for i, r in enumerate(ranges):
        bearing = angle_min + i * angle_increment
        if abs(bearing) > cone_rad or not math.isfinite(r) or not min_m <= r <= max_m:
            continue
        if best is None or r < best[0]:
            best = (r, bearing)
    return best
