"""Rendered depth frames of known ground, for testing the 3D camera reader without Gazebo.

The world is the same across the path, so the depth of a pixel depends only on its row: each row's
ray is marched forward until it is at or below the ground. Ground shapes are functions from the
distance ahead (an array, in metres) to the ground height there.
"""

from __future__ import annotations

import math

import numpy as np

from mana_perception.depth import Camera

# The 3D camera planned for the chair: 1.0 m up, tilted 0.6 rad down, 87 degrees across. The
# image is 4:3 with square pixels, so about 70 degrees up and down, and the ground is out of view
# closer than about 0.37 m (the blind zone).
COLS, ROWS = 160, 120
CAMERA = Camera.from_fov(COLS, ROWS, math.radians(87.0), height_m=1.0, pitch_rad=0.6)


def render(ground, cam: Camera = CAMERA, rows=ROWS, cols=COLS, far=8.0) -> np.ndarray:
    """Depth along the optical axis, in metres; inf where a ray meets nothing within far."""
    z = np.arange(0.05, far, 0.002)
    c, s = math.cos(cam.pitch_rad), math.sin(cam.pitch_rad)
    image = np.full((rows, cols), np.inf)
    for v in range(rows):
        yn = (v - cam.cy) / cam.fy
        ahead = z * (c - yn * s)
        height = cam.height_m - z * (yn * c + s)
        hit = np.nonzero(height <= ground(ahead))[0]
        if hit.size:
            image[v, :] = z[hit[0]]
    return image


def noisy(image: np.ndarray, rng, noise=0.01, missing=0.1) -> np.ndarray:
    """Depth noise as a share of the depth, and a share of pixels with no reading."""
    out = image * (1.0 + rng.normal(0.0, noise, image.shape))
    out[rng.random(image.shape) < missing] = np.nan
    return out


def flat(x):
    return np.zeros_like(x)


def ramp(start, slope):
    return lambda x: np.where(x > start, (x - start) * slope, 0.0)


def step(at, height):
    """One step up (height > 0) or down (height < 0) at this distance."""
    return lambda x: np.where(x >= at, height, 0.0)


def stairs(start, rise, run=0.28, count=8):
    """Stairs up (rise > 0) or down (rise < 0), the first edge at start."""
    return lambda x: rise * np.clip(np.floor((x - start) / run) + 1, 0, count)
