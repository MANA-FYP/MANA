"""What the simulated sensors say, turned into what the core reads. Pure Python, no ROS: the node in
sensors_node.py only moves the numbers in and out of messages.
"""

import math

WHEEL_RADIUS_M = 0.15


def tilt(x: float, y: float, z: float, w: float) -> tuple[float, float]:
    """(pitch, roll) in radians from an orientation quaternion. REP 103: pitch > 0 is nose down."""
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    return pitch, roll


def wheel_slip(
    wheel_rad_s: list[float],
    ground_mps: float,
    limit_mps: float = 0.3,
    min_mps: float = 0.2,
) -> bool:
    """The wheels turn much faster (or slower) than the chair moves: they slip. The mean of the
    wheel speeds is signed, so on a turn on the spot, when the two sides turn opposite ways, it
    cancels and is not read as slip. Below min_mps there is too little movement to tell."""
    if not wheel_rad_s:
        return False
    wheels_mps = abs(sum(wheel_rad_s) / len(wheel_rad_s)) * WHEEL_RADIUS_M
    if max(wheels_mps, abs(ground_mps)) < min_mps:
        return False
    return abs(wheels_mps - abs(ground_mps)) > limit_mps


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
