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
    """The wheels turn much faster (or slower) than the chair moves: they slip. Below min_mps there
    is too little movement to tell."""
    if not wheel_rad_s:
        return False
    wheels_mps = sum(abs(w) for w in wheel_rad_s) / len(wheel_rad_s) * WHEEL_RADIUS_M
    if max(wheels_mps, abs(ground_mps)) < min_mps:
        return False
    return abs(wheels_mps - abs(ground_mps)) > limit_mps


def local_xy(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    """East and north in metres of a GPS fix from the origin. Flat earth: good to a few centimetres
    over the 50 m of the route."""
    r = 6378137.0
    north = math.radians(lat - origin_lat) * r
    east = math.radians(lon - origin_lon) * r * math.cos(math.radians(origin_lat))
    return east, north


def gps_error_m(cov_xx: float, cov_yy: float, default_m: float) -> float:
    """The horizontal error from the covariance of the fix; default_m if the sensor gives none."""
    var = max(cov_xx, 0.0) + max(cov_yy, 0.0)
    return math.sqrt(var) if var > 0.0 else default_m


def gps_trust(error_m: float, bad_m: float = 10.0) -> float:
    """1 for a perfect fix, falling to 0 at bad_m."""
    return max(0.0, min(1.0, 1.0 - error_m / bad_m))


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
