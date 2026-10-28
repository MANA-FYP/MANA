"""The sensor faults of the simulation: which are on, and what each does to a number or an image.
Pure Python (and numpy for the images), no ROS: fault_node.py applies it to the topics.

Every fault is switched on and off during a run. They are made outside Gazebo, on the messages, so
they work the same on a recording.

- noise (camera, depth, lidar, imu, gps): adds Gaussian noise; a GPS gets the matching covariance
- dropout (the same sensors): the sensor sends nothing; a GPS dropout is the GPS loss
- dark (camera): the image keeps a share of its light
- fade (wifi): the Wi-Fi heartbeats get lost more and more, until none gets through
- slip (wheels): the encoders say the wheels turn faster than the chair moves
"""

import math
import random
from dataclasses import dataclass

import numpy as np

KINDS = {
    "noise": {"camera", "depth", "lidar", "imu", "gps"},
    "dropout": {"camera", "depth", "lidar", "imu", "gps"},
    "dark": {"camera"},
    "fade": {"wifi"},
    "slip": {"wheels"},
}
# the usual size of the noise: grey levels, metres, metres, rad/s and m/s^2, metres
NOISE_SIGMA = {"camera": 20.0, "depth": 0.05, "lidar": 0.05, "imu": 0.05, "gps": 3.0}
DEFAULT_VALUE = {"noise": 1.0, "dark": 0.05, "fade": 10.0, "slip": 2.0, "dropout": 1.0}
DEGREES_PER_METRE = 1.0 / 111320.0


@dataclass(frozen=True)
class Active:
    value: float
    since: float  # simulation seconds


class Faults:
    def __init__(self) -> None:
        self.on: dict[tuple[str, str], Active] = {}

    def set(
        self, sensor: str, kind: str, active: bool, value: float, now: float
    ) -> tuple[bool, str]:
        """Switch a fault on or off. Returns (ok, message)."""
        if kind not in KINDS:
            return False, f"unknown kind {kind!r}; choose from {', '.join(KINDS)}"
        if sensor not in KINDS[kind]:
            return False, f"{kind} is for {', '.join(sorted(KINDS[kind]))}, not {sensor!r}"
        if active:
            strength = value if math.isfinite(value) and value > 0.0 else DEFAULT_VALUE[kind]
            self.on[(sensor, kind)] = Active(strength, now)
        else:
            self.on.pop((sensor, kind), None)
        return True, self.summary()

    def summary(self) -> str:
        return ", ".join(f"{s} {k}" for s, k in sorted(self.on)) or "none"

    def dropped(self, sensor: str) -> bool:
        return (sensor, "dropout") in self.on

    def noise_sigma(self, sensor: str) -> float:
        """The size of the noise on this sensor now; 0 when there is none."""
        f = self.on.get((sensor, "noise"))
        return NOISE_SIGMA[sensor] * f.value if f else 0.0

    def light_left(self) -> float:
        """The share of the light the camera keeps: 1 when it is not dark."""
        f = self.on.get(("camera", "dark"))
        return min(f.value, 1.0) if f else 1.0

    def slip_factor(self) -> float:
        f = self.on.get(("wheels", "slip"))
        return f.value if f else 1.0

    def wifi_quality(self, now: float) -> float:
        """The share of the Wi-Fi heartbeats that get through: 1 for a good link, falling to 0 over
        the fade time."""
        f = self.on.get(("wifi", "fade"))
        if f is None:
            return 1.0
        return 1.0 - min(1.0, max(0.0, (now - f.since) / f.value))


def noisy(values: list[float], sigma: float, rng: random.Random) -> list[float]:
    """The numbers with noise added; echoes and values that are not finite stay as they are."""
    if sigma <= 0.0:
        return values
    return [v + rng.gauss(0.0, sigma) if math.isfinite(v) else v for v in values]


def noisy_gps(lat: float, lon: float, sigma_m: float, rng: random.Random) -> tuple[float, float]:
    """A fix moved by Gaussian noise of sigma_m metres."""
    north = rng.gauss(0.0, sigma_m) * DEGREES_PER_METRE
    east = rng.gauss(0.0, sigma_m) * DEGREES_PER_METRE / max(math.cos(math.radians(lat)), 1e-6)
    return lat + north, lon + east


def noisy_image(data: bytes, encoding: str, sigma: float, rng: np.random.Generator) -> bytes:
    """A camera image (rgb8, 8 bits) or a depth image (32FC1) with Gaussian noise."""
    if sigma <= 0.0:
        return data
    if encoding == "32FC1":
        a = np.frombuffer(data, dtype=np.float32)
        out = np.where(np.isfinite(a), a + rng.normal(0.0, sigma, a.shape), a)
        return out.astype(np.float32).tobytes()
    a = np.frombuffer(data, dtype=np.uint8).astype(np.float32)
    return np.clip(a + rng.normal(0.0, sigma, a.shape), 0, 255).astype(np.uint8).tobytes()


def darkened(data: bytes, light_left: float) -> bytes:
    """An 8-bit image with only light_left of its light (1 leaves it as it is)."""
    if light_left >= 1.0:
        return data
    a = np.frombuffer(data, dtype=np.uint8).astype(np.float32) * max(light_left, 0.0)
    return a.astype(np.uint8).tobytes()
