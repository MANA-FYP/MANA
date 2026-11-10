"""The open test world (#79): a small campus with every hazard, reachable in any order and from
any side, made from simple boxes. Pure Python, no ROS.

    python -m mana_sim.campus > worlds/mana_campus.sdf              # the default campus
    python -m mana_sim.campus --seed 7 --start before_steps > /tmp/campus7.sdf

The layout (metres; x east, y north; the street at z = 0):

    y -16 to -6   the street, with a curb cut at x 20 to 22
    y  -6 to 34   the plaza, a curb above the street
    building      x 0 to 24, y 6 to 26, its floor 3 risers above the plaza:
                  steps and a ramp at the south door, stairs down at the north door,
                  a lobby, a corridor, a dark room (roofed, no door light), a room and a hall
                  with tables and chairs
    terrace       x -26 to -12, y 6 to 20 (further north when its ramp is long), a drop above
                  the plaza on three sides, a ramp up from the south

A seed varies what the real world varies, within the ranges of #79: the step height (10 to
20 cm), the curb, the ramp slopes, the drop, the colours of the floors and the sunlight. The
default campus (no seed) is the one in worlds/mana_campus.sdf. Zones (indoors, the dark room, a
weak Wi-Fi area) are written down here for the faults to switch on (#15); the world does not
switch them itself.
"""

import argparse
import math
import random
import sys
from dataclasses import dataclass, field

CHAIR_Z = 0.38  # the chair's origin above the ground it stands on (worlds/mana_route.sdf)
WALL_H = 2.5
WALL_T = 0.2
TREAD = 0.33  # the depth of a step, as on the route


@dataclass(frozen=True)
class Variation:
    riser_m: float = 0.17  # each of the three steps (the building floor is 3 risers up)
    curb_m: float = 0.12
    entrance_ramp_deg: float = 4.8  # about 1:12, the usual most for a public ramp
    terrace_ramp_deg: float = 7.0  # the route's ramp
    drop_m: float = 1.0  # the terrace above the plaza
    sun: float = 0.9  # the sunlight's strength
    shade: float = 0.0  # floors lighter (+) or darker (-), up to 0.15

    @classmethod
    def from_seed(cls, seed: int) -> "Variation":
        r = random.Random(seed)
        return cls(
            riser_m=round(r.uniform(0.10, 0.20), 3),
            curb_m=round(r.uniform(0.10, 0.20), 3),
            entrance_ramp_deg=round(r.uniform(3.0, 6.0), 1),
            terrace_ramp_deg=round(r.uniform(5.0, 9.0), 1),
            drop_m=round(r.uniform(0.5, 1.5), 2),
            sun=round(r.uniform(0.5, 1.0), 2),
            shade=round(r.uniform(-0.15, 0.15), 2),
        )


@dataclass(frozen=True)
class Box:
    """An axis-aligned box: x0..x1, y0..y1, z0..z1."""

    name: str
    x0: float
    x1: float
    y0: float
    y1: float
    z0: float
    z1: float
    colour: tuple[float, float, float]
    collide: bool = True


@dataclass(frozen=True)
class Ramp:
    """A slab whose top runs from z_low to z_high across its footprint, rising toward `rises`
    ("+x", "-x", "+y" or "-y")."""

    name: str
    x0: float
    x1: float
    y0: float
    y1: float
    z_low: float
    z_high: float
    rises: str
    colour: tuple[float, float, float]
    thick: float = 0.1

    def top(self, x: float, y: float) -> float:
        """The height of its top at (x, y) inside its footprint."""
        axis, lo, hi = (x, self.x0, self.x1) if "x" in self.rises else (y, self.y0, self.y1)
        f = (axis - lo) / (hi - lo)
        f = f if self.rises[0] == "+" else 1.0 - f
        return self.z_low + f * (self.z_high - self.z_low)

    def pose(self) -> tuple[tuple[float, float, float, float, float, float], tuple]:
        """The pose and size of the slab, its top surface on the slope."""
        run = (self.x1 - self.x0) if "x" in self.rises else (self.y1 - self.y0)
        rise = self.z_high - self.z_low
        a = math.atan2(rise, run)
        length = math.hypot(run, rise)
        sign = 1.0 if self.rises[0] == "+" else -1.0
        cx, cy = (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2
        cz = self.z_low + rise / 2 - self.thick / 2 * math.cos(a)
        back = self.thick / 2 * math.sin(a) * sign  # the slab's centre is below the slope's
        if "x" in self.rises:
            return (cx + back, cy, cz, 0.0, -a * sign, 0.0), (length, self.y1 - self.y0, self.thick)
        return (cx, cy + back, cz, a * sign, 0.0, 0.0), (self.x1 - self.x0, length, self.thick)


@dataclass
class Campus:
    variation: Variation
    boxes: list[Box] = field(default_factory=list)
    ramps: list[Ramp] = field(default_factory=list)
    starts: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    zones: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    roofed: list[str] = field(default_factory=list)

    @property
    def plaza(self) -> float:
        return self.variation.curb_m

    @property
    def floor(self) -> float:
        return self.plaza + 3 * self.variation.riser_m

    @property
    def terrace(self) -> float:
        return self.plaza + self.variation.drop_m

    def ground(self, x: float, y: float) -> float | None:
        """The height of the walkable surface at (x, y): the highest top of a box or ramp there
        no more than 0.25 m above the one below (a wall or a table is not ground); None off the
        campus."""
        tops = [b.z1 for b in self.boxes if b.collide and b.x0 <= x <= b.x1 and b.y0 <= y <= b.y1]
        tops += [r.top(x, y) for r in self.ramps if r.x0 <= x <= r.x1 and r.y0 <= y <= r.y1]
        return max(tops) if tops else None

    def blocked(self, x: float, y: float, half: float, z: float) -> list[str]:
        """The boxes higher than a step over z within `half` metres of (x, y): what a chair of
        that size would stand in."""
        return [
            b.name
            for b in self.boxes
            if b.collide
            and b.z1 > z + 0.05
            and b.z0 < z + 1.5
            and b.x0 < x + half
            and b.x1 > x - half
            and b.y0 < y + half
            and b.y1 > y - half
        ]


def _shade(rgb: tuple[float, float, float], d: float) -> tuple[float, float, float]:
    return tuple(round(min(1.0, max(0.0, c + d)), 3) for c in rgb)


def build(v: Variation | None = None) -> Campus:
    v = v or Variation()
    c = Campus(v)
    B, R = c.boxes.append, c.ramps.append
    asphalt = _shade((0.32, 0.32, 0.34), v.shade)
    paving = _shade((0.62, 0.6, 0.55), v.shade)
    floor_c = _shade((0.72, 0.68, 0.6), v.shade)
    wall_c = (0.85, 0.85, 0.82)
    step_c = (0.55, 0.55, 0.6)
    ramp_c = (0.75, 0.62, 0.25)
    wood = (0.5, 0.35, 0.2)
    p, f, t = c.plaza, c.floor, c.terrace

    # the ground: the street, the plaza a curb above it, and a low wall round the campus
    B(Box("street", -35, 35, -16, -6, -0.1, 0.0, asphalt))
    B(Box("plaza", -35, 35, -6, 34, -0.1, p, paving))
    B(Box("curb_edge", -35, 35, -6.1, -6.0, 0.0, p, (0.8, 0.8, 0.8), collide=False))
    R(Ramp("curb_cut", 20, 22, -7.5, -6, 0.0, p, "+y", ramp_c))
    for name, box in {
        "edge_south": (-35, 35, -16.3, -16, 0.0, 1.0),
        "edge_north": (-35, 35, 34, 34.3, p, p + 1.0),
        "edge_west": (-35.3, -35, -16, 34, 0.0, p + 1.0),
        "edge_east": (35, 35.3, -16, 34, 0.0, p + 1.0),
    }.items():
        B(Box(name, *box, wall_c))

    # the building: its floor slab, three risers above the plaza
    B(Box("building_floor", 0, 24, 6, 26, p, f, floor_c))
    r = v.riser_m
    # the south door, x 8 to 16: steps at x 8 to 12, a ramp at x 12 to 16
    B(Box("steps_up_1", 8, 12, 6 - 2 * TREAD, 6, p, p + r, step_c))
    B(Box("steps_up_2", 8, 12, 6 - TREAD, 6, p, p + 2 * r, step_c))
    run = (f - p) / math.tan(math.radians(v.entrance_ramp_deg))
    R(Ramp("entrance_ramp", 12, 16, 6 - run, 6, p, f, "+y", ramp_c))
    # the north door, x 16 to 20: stairs down to the plaza
    B(Box("stairs_down_1", 16, 20, 26, 26 + TREAD, p, p + 2 * r, step_c))
    B(Box("stairs_down_2", 16, 20, 26 + TREAD, 26 + 2 * TREAD, p, p + r, step_c))

    def wall(name, x0, x1, y0, y1, gaps=()):
        """A wall along x (y0 == y1) or along y (x0 == x1), with doorways (from, to)."""
        along_x = y0 == y1
        lo, hi = (x0, x1) if along_x else (y0, y1)
        cuts = [lo, *[e for g in sorted(gaps) for e in g], hi]
        for i in range(0, len(cuts), 2):
            a, b = cuts[i], cuts[i + 1]
            if b - a < 1e-6:
                continue
            if along_x:
                B(
                    Box(
                        f"{name}_{i // 2}",
                        a,
                        b,
                        y0 - WALL_T / 2,
                        y0 + WALL_T / 2,
                        f,
                        f + WALL_H,
                        wall_c,
                    )
                )
            else:
                B(
                    Box(
                        f"{name}_{i // 2}",
                        x0 - WALL_T / 2,
                        x0 + WALL_T / 2,
                        a,
                        b,
                        f,
                        f + WALL_H,
                        wall_c,
                    )
                )

    wall("wall_south", 0, 24, 6, 6, gaps=[(8, 16)])
    wall("wall_north", 0, 24, 26, 26, gaps=[(16, 20)])
    wall("wall_west", 0, 0, 6, 26)
    wall("wall_east", 24, 24, 6, 26)
    # inside: a lobby (x 6 to 18) open to the corridor (y 14 to 17), the dark room west of it and
    # a room east of it, each with a 1 m door; the hall north of the corridor, two doorways
    wall("wall_dark_room", 6, 6, 6, 14, gaps=[(9, 10)])
    wall("wall_dark_corridor", 0, 6, 14, 14)
    wall("wall_room", 18, 18, 6, 14, gaps=[(9, 10)])
    wall("wall_room_corridor", 18, 24, 14, 14)
    wall("wall_hall", 0, 24, 17, 17, gaps=[(2, 3.5), (10, 14)])
    B(Box("dark_room_roof", 0, 6, 6, 14, f + WALL_H, f + WALL_H + 0.1, wall_c))
    c.roofed.append("dark_room")
    for i, (x, y) in enumerate([(20, 8), (20, 11.5), (22.5, 8)]):  # the room: tables
        B(Box(f"table_room_{i}", x, x + 1.2, y, y + 0.8, f, f + 0.75, wood))
    for i, (x, y) in enumerate([(3, 20), (7, 20), (3, 23), (7, 23), (11, 22)]):  # the hall
        B(Box(f"table_hall_{i}", x, x + 1.6, y, y + 0.8, f, f + 0.75, wood))
        B(Box(f"chair_hall_{i}", x + 0.5, x + 1.0, y - 0.6, y - 0.1, f, f + 0.45, wood))
    B(Box("bench_plaza", -6, -4, 0, 0.5, p, p + 0.45, wood))
    B(Box("planter_plaza", 26, 28, 0, 2, p, p + 0.6, (0.3, 0.5, 0.25)))

    # the terrace, 14 m deep: a drop on the west, north and east, a ramp up from the south. A long
    # ramp (a high drop, a gentle slope) moves it north, so the ramp starts 3 m inside the curb
    terrace_run = (t - p) / math.tan(math.radians(v.terrace_ramp_deg))
    ty = max(6.0, -3.0 + terrace_run)
    B(Box("terrace", -26, -12, ty, ty + 14, p, t, paving))
    R(Ramp("terrace_ramp", -21, -17, ty - terrace_run, ty, p, t, "+y", ramp_c))
    stripe = (0.9, 0.1, 0.1)
    B(Box("terrace_edge_stripe", -26, -12, ty + 13.8, ty + 14, t, t + 0.004, stripe, collide=False))
    entrance_run = (f - p) / math.tan(math.radians(v.entrance_ramp_deg))

    # where a run can start: x, y, yaw (rad), and the ground there (the chair stands on it)
    starts = {
        "plaza": (-2.0, 2.0, math.pi / 2),
        "street": (-4.0, -11.0, math.pi / 2),  # facing the curb
        "before_curb_cut": (21.0, -11.0, math.pi / 2),
        "before_steps": (10.0, 1.0, math.pi / 2),  # the stairs up, 3 m before the first step
        # 3 m before a ramp, but on the plaza, not in the street below its curb
        "before_entrance_ramp": (14.0, max(6 - entrance_run - 3, -5.0), math.pi / 2),
        "lobby": (12.0, 9.0, math.pi / 2),
        "corridor": (2.0, 15.5, 0.0),
        "outside_dark_room": (8.0, 9.5, math.pi),
        "hall": (18.0, 21.0, math.pi / 2),  # facing the stairs down at the north door
        "behind_building": (18.0, 31.0, -math.pi / 2),  # facing the stairs up from the north
        "before_terrace_ramp": (-19.0, max(ty - terrace_run - 3, -5.0), math.pi / 2),
        "terrace": (-19.0, ty + 7, math.pi / 2),  # facing the north edge, the drop
    }
    # the ten Gazebo scenarios (mana_sim.gazebo_scenarios), each where its hazard is
    for scenario, at in {
        "level": "plaza",
        "ramp": "before_entrance_ramp",
        "stairs": "before_steps",
        "curb": "street",
        "stop": "plaza",  # a person ahead on level ground
        "drop": "terrace",
        "manual_drop": "terrace",
        "picture": "plaza",
        "dark": "outside_dark_room",  # here the room is dark, not a camera fault on level ground
        "imu_lost": "before_steps",
    }.items():
        starts[f"scenario_{scenario}"] = starts[at]
    for name, (x, y, yaw) in starts.items():
        c.starts[name] = (round(x, 3), round(y, 3), round(yaw, 4), round(c.ground(x, y), 3))
    c.zones = {
        "indoors": (0, 24, 6, 26),  # GPS lost (#15)
        "dark_room": (0, 6, 6, 14),  # darkness (#15)
        "weak_wifi": (0, 24, 17, 26),  # the hall: Wi-Fi fades (#15)
    }
    return c


def _f(v: float) -> str:
    return f"{round(v, 4) + 0.0:g}"  # to 0.1 mm; + 0.0 turns -0.0 into 0


def _material(rgb) -> str:
    amb = " ".join(_f(x * 0.8) for x in rgb)
    dif = " ".join(_f(x) for x in rgb)
    return f"<material><ambient>{amb} 1</ambient><diffuse>{dif} 1</diffuse></material>"


def _shape(name: str, pose, size, rgb, collide: bool) -> str:
    pose_s = " ".join(_f(round(x, 4)) for x in pose)
    size_s = " ".join(_f(round(x, 4)) for x in size)
    geo = f"<geometry><box><size>{size_s}</size></box></geometry>"
    out = []
    if collide:
        out.append(f'        <collision name="{name}"><pose>{pose_s}</pose>{geo}</collision>')
    out.append(f'        <visual name="{name}"><pose>{pose_s}</pose>{geo}{_material(rgb)}</visual>')
    return "\n".join(out)


def to_sdf(c: Campus, start: str = "plaza", seed: int | None = None) -> str:
    v = c.variation
    shapes = []
    for b in c.boxes:
        pose = ((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2, (b.z0 + b.z1) / 2, 0, 0, 0)
        shapes.append(
            _shape(b.name, pose, (b.x1 - b.x0, b.y1 - b.y0, b.z1 - b.z0), b.colour, b.collide)
        )
    for r in c.ramps:
        pose, size = r.pose()
        shapes.append(_shape(r.name, pose, size, r.colour, True))
    x, y, yaw, ground = c.starts[start]
    sun = _f(v.sun)
    starts = "\n".join(
        f"    {n:<30} x {s[0]:7.2f}  y {s[1]:7.2f}  yaw {s[2]:6.3f}  ground {s[3]:.3f}"
        for n, s in c.starts.items()
    )
    zones = "\n".join(
        f"    {n:<12} x {z[0]} to {z[1]}, y {z[2]} to {z[3]}" for n, z in c.zones.items()
    )
    return f"""<?xml version="1.0"?>
<!--
  MANA open test world (#79): a small campus with every hazard, made by mana_sim/campus.py
  (python -m mana_sim.campus, seed {seed}). Do not edit by hand.

  Steps {v.riser_m} m, curb {v.curb_m} m, entrance ramp {v.entrance_ramp_deg} deg,
  terrace ramp {v.terrace_ramp_deg} deg, drop {v.drop_m} m, sun {v.sun}, floor shade {v.shade}.
  Heights: street 0, plaza {_f(c.plaza)}, building floor {_f(c.floor)}, terrace {_f(c.terrace)}.

  Start poses (the chair stands at {start}; the start option of mana_sim.campus picks another):
{starts}

  Zones, for the faults (#15) to switch:
{zones}
-->
<sdf version="1.9">
  <world name="mana_campus">
    <physics name="4ms" type="dart">
      <max_step_size>0.004</max_step_size>
      <real_time_factor>1.0</real_time_factor>
    </physics>
    <plugin filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
    <plugin filename="gz-sim-user-commands-system" name="gz::sim::systems::UserCommands"/>
    <plugin filename="gz-sim-scene-broadcaster-system" name="gz::sim::systems::SceneBroadcaster"/>
    <!-- the chair's sensors: cameras and lidar need rendering, IMU and GPS do not -->
    <plugin filename="gz-sim-sensors-system" name="gz::sim::systems::Sensors">
      <render_engine>ogre2</render_engine>
    </plugin>
    <plugin filename="gz-sim-imu-system" name="gz::sim::systems::Imu"/>
    <plugin filename="gz-sim-navsat-system" name="gz::sim::systems::NavSat"/>

    <!-- a made-up place, only so that the GPS has a position (as worlds/mana_route.sdf) -->
    <spherical_coordinates>
      <surface_model>EARTH_WGS84</surface_model>
      <latitude_deg>33.6844</latitude_deg>
      <longitude_deg>73.0479</longitude_deg>
      <elevation>540</elevation>
      <heading_deg>0</heading_deg>
    </spherical_coordinates>
    <gravity>0 0 -9.81</gravity>
    <scene>
      <ambient>0.5 0.5 0.5 1</ambient>
      <background>0.75 0.85 0.95 1</background>
      <shadows>true</shadows>
    </scene>
    <light type="directional" name="sun">
      <cast_shadows>true</cast_shadows>
      <pose>0 0 30 0 0 0</pose>
      <diffuse>{sun} {sun} {sun} 1</diffuse>
      <specular>0.3 0.3 0.3 1</specular>
      <direction>-0.4 0.2 -0.9</direction>
    </light>

    <model name="campus">
      <static>true</static>
      <link name="ground">
{chr(10).join(shapes)}
      </link>
    </model>

    <include>
      <uri>model://mana_chair</uri>
      <name>mana_chair</name>
      <pose>{_f(x)} {_f(y)} {_f(round(ground + CHAIR_Z, 3))} 0 0 {_f(yaw)}</pose>
    </include>
  </world>
</sdf>
"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Write the campus world (#79) as SDF")
    p.add_argument("--seed", type=int, help="vary the world (step height, slopes, light...)")
    p.add_argument("--start", default="plaza", help="where the chair starts (a name in the file)")
    a = p.parse_args(argv)
    c = build(None if a.seed is None else Variation.from_seed(a.seed))
    if a.start not in c.starts:
        p.error(f"no start {a.start!r}: one of {', '.join(c.starts)}")
    sys.stdout.write(to_sdf(c, a.start, a.seed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
