"""Score the 3D camera ground reader on rendered frames, for the table in the package README.

    python3 tools/evaluate_depth_ground.py

Every ground shape is rendered at distances from 0.1 to 2.4 m and camera tilts of 0.5 to 0.7 rad,
with 1 % depth noise and 10 % of pixels missing. A reading counts as the right type when it names
the shape at the right distance, and as found when it is any step or drop at the right distance.
The right distance is within 15 cm; for an edge in the camera's blind zone, where the camera
cannot see where the edge is, it is any distance no farther than the edge itself.
"""

import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "mana_perception"))

from mana_perception import depth as dp  # noqa: E402
from mana_perception import synthetic as syn  # noqa: E402

STEPS = (dp.CURB, dp.STAIRS_UP, dp.STAIRS_DOWN, dp.DROP)
NAMES = {v: k for k, v in vars(dp).items() if k.isupper() and isinstance(v, int)}


def main() -> int:
    rng = np.random.default_rng(1)
    right, found, total = defaultdict(int), defaultdict(int), defaultdict(int)
    blind_total, blind_found, unsafe = 0, 0, 0
    for pitch in (0.5, 0.6, 0.7):
        cam = dp.Camera.from_fov(syn.COLS, syn.ROWS, math.radians(87.0), 1.0, pitch)
        blind = dp.nearest_ground(cam, syn.ROWS)
        for at in np.arange(0.1, 2.45, 0.1):
            shapes = [
                ("curb", syn.step, dp.CURB, (0.10, 0.15, 0.19)),
                ("stairs up", syn.stairs, dp.STAIRS_UP, (0.15, 0.17, 0.20)),
                ("stairs down", lambda a, h: syn.stairs(a, -h), dp.STAIRS_DOWN, (0.15, 0.17, 0.20)),
                ("drop", lambda a, h: syn.step(a, -h), dp.DROP, (0.30, 0.32, 0.35)),
            ]
            for name, shape, kind, heights in shapes:
                for height in heights:
                    image = syn.noisy(syn.render(shape(at, height), cam), rng)
                    r = dp.read_ground(image, cam)
                    near = abs(r.distance_m - at) <= 0.15 or (at < blind and r.distance_m <= at)
                    total[name] += 1
                    right[name] += r.ground == kind and near
                    found[name] += r.ground in STEPS and near
                    # unsafe: missed, or put more than 15 cm past where the edge really is
                    unsafe += r.ground not in STEPS or r.distance_m > at + 0.15
                    if at < blind:
                        blind_total += 1
                        blind_found += r.ground in STEPS and r.distance_m <= at
        flats = [
            ("flat", syn.flat, dp.FLAT),
            ("ramp 8 %", syn.ramp(1.5, 0.08), dp.RAMP),
            ("ramp -8 %", syn.ramp(1.5, -0.08), dp.RAMP),
            ("slope 30 %", syn.ramp(1.3, 0.3), dp.RAMP),
        ]
        for name, shape, kind in flats:
            for _ in range(20):
                r = dp.read_ground(syn.noisy(syn.render(shape, cam), rng), cam)
                total[name] += 1
                right[name] += r.ground == kind
                found[name] += r.ground in STEPS
    print(f"{'ground':12s} {'right type':>12s} {'step or drop found':>20s}")
    for name in total:
        print(f"{name:12s} {right[name]:5d} / {total[name]:<4d} {found[name]:9d} / {total[name]}")
    print(f"edges in the blind zone, found no farther than they are: {blind_found} / {blind_total}")
    print(f"steps and drops missed, or put more than 15 cm too far: {unsafe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
