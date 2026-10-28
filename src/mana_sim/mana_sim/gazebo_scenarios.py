"""The scripted scenarios of mana_core, played on the Gazebo route with the same pass marks.

mana_core.scenarios has 1-D routes and marks. Here each of the ten original scenarios gets a
start and an end on the route of worlds/mana_route.sdf, the objects it needs, and what is
switched when. Every mark is the same function with the same limit as in the scripted scenario
(`no_tracks`, `tracks_before`, `slower_on`, `stops_before` ...); only the distances in them are the
ones of this route, in metres from the start of the run. `check(scenario, trace)` then scores a
trace recorded in Gazebo exactly as it scores a scripted one.

Pure Python, no ROS. scenario_runner.py records the traces, gazebo_run.py plays a scenario.
"""

import json
import math
from dataclasses import dataclass, field

from mana_core.decision import Command, Ground, Mode, Priority, Source
from mana_core.messages import Message as Heard
from mana_core.scenarios import Message, Sample, Say, Scenario, Trace
from mana_core.world import Fault, Obstacle, Route, Stretch

from mana_core import scenarios as sc

# the hazards of the route, metres along it (worlds/mana_route.sdf)
STEP_ZONES = (("stairs", 22.0, 22.7), ("curb", 31.0, 31.3))  # on wheels here is a mishap
DROP_X = 38.0
FELL_Z = -0.3  # the chassis this low: it went over the drop


@dataclass(frozen=True)
class Box:
    """A box added to the world: a person, a wall. x, y, z is its centre."""

    name: str
    x: float
    size: tuple[float, float, float]
    y: float = 0.0
    color: tuple[float, float, float] = (0.8, 0.3, 0.2)

    @property
    def z(self) -> float:
        return self.size[2] / 2


@dataclass(frozen=True)
class FaultStep:
    """Switch a fault (SetFault) once the run has lasted at_s seconds, or the chair has gone
    at_x metres from the start, whichever the step names."""

    sensor: str
    kind: str
    active: bool = True
    value: float = 0.0
    at_s: float | None = None
    at_x: float | None = None


@dataclass(frozen=True)
class GazeboRun:
    scripted: str  # the name in mana_core.scenarios.SCENARIOS
    start_x: float  # where the chair starts on the route
    end_x: float
    camera: Route  # what the camera stand-in reports, in metres from start_x
    marks: tuple[sc.Mark, ...]
    duration_s: float
    boxes: tuple[Box, ...] = ()
    faults: tuple[FaultStep, ...] = ()
    says: tuple[Say, ...] = ()
    note: str = ""  # how the route differs from the scripted one

    @property
    def length_m(self) -> float:
        return self.end_x - self.start_x

    def scripted_route(self) -> Route:
        """The route as the scripted world would play it: the camera route and the faults of the
        run (a dropout is no reports, darkness a camera trust of 0.2)."""
        faults = tuple(
            Fault(
                f.sensor,
                from_m=f.at_x or 0.0,
                trust=None if f.kind == "dropout" else 0.2,
            )
            for f in self.faults
            if f.active
        )
        return Route(
            self.camera.length_m,
            self.camera.stretches,
            self.camera.obstacles,
            self.camera.faults + faults,
        )

    def scenario(self) -> Scenario:
        """The scenario to score a recorded trace with: the scripted one's text, these marks."""
        base = sc.SCENARIOS[self.scripted]
        return Scenario(
            base.name,
            base.title,
            base.shows,
            self.scripted_route(),
            self.duration_s,
            self.says,
            self.marks,
        )

    def obstacles(self) -> list[tuple[float, str]]:
        """(metres from the start, label) of the boxes that count as something to hit."""
        return [(b.x - self.start_x, b.name) for b in self.boxes]


# ---- the ten scenarios on the route

PERSON = Box("person", 7.0, (0.4, 0.6, 1.7))
STAIRS_AT = 3.8  # the stairs, 22 m along the route, from a start at 18.2 m (the ramp ends at 18 m)
STAIRS_TOP = 4.5  # the top of the third step
CAM_STAIRS = Stretch(STAIRS_AT, STAIRS_TOP, Ground.STAIRS_UP, pitch_rad=0.55, height_m=0.17)

RUNS = {
    r.scripted: r
    for r in (
        GazeboRun(
            "level",
            start_x=-1.5,
            end_x=3.0,
            camera=Route(4.5),
            duration_s=12.0,
            marks=(sc.no_tracks(), sc.full_speed(), sc.reaches_end(4.5)),
            note="level ground for 4.5 m: after that the ramp at 8 m is in view of the 3D camera",
        ),
        GazeboRun(
            "ramp",
            start_x=2.0,
            end_x=14.0,
            camera=Route(12.0, (Stretch(6.0, 10.0, Ground.RAMP, pitch_rad=0.12),)),
            duration_s=60.0,
            marks=(sc.no_tracks(), sc.slower_on(6.0, 10.0, "ramp"), sc.reaches_end(12.0)),
            note="the ramp is 4 m long (8 to 12 m) and rises 0.5 m, then the plateau",
        ),
        GazeboRun(
            "stairs",
            start_x=18.2,
            end_x=25.0,
            camera=Route(6.8, (CAM_STAIRS,)),
            duration_s=90.0,
            marks=(sc.tracks_before(STAIRS_AT), sc.wheels_after(5.7), sc.reaches_end(6.8)),
            note="3 steps of 17 cm in 0.66 m; the run ends 1 m before the stairs down",
        ),
        GazeboRun(
            "curb",
            start_x=27.0,
            end_x=33.5,
            camera=Route(6.5, (Stretch(4.0, 4.3, Ground.CURB, pitch_rad=0.3, height_m=0.12),)),
            duration_s=60.0,
            marks=(sc.tracks_before(4.0), sc.wheels_after(6.0), sc.reaches_end(6.5)),
            note="the 12 cm curb is the edge of a platform 3 m long, then a step down",
        ),
        GazeboRun(
            "stop",
            start_x=-1.0,
            end_x=8.0,
            camera=Route(20.0),
            duration_s=7.0,
            says=(Say(2.0, Command.STOP), Say(5.0, Command.GO)),
            marks=(
                sc.stop_within(),
                sc.stopped_until_go(),
                sc.moves_after_go(),
                sc.tells("Stopped by you"),
            ),
        ),
        GazeboRun(
            "drop",
            start_x=34.5,
            end_x=38.0,
            camera=Route(3.5, (Stretch(3.5, 4.5, Ground.DROP),)),
            duration_s=30.0,
            marks=(sc.stops_before(3.5, "drop"), sc.tells("Drop ahead, stopping")),
            note="the drop is at 38 m, 4 m after the curb; a drop is 1 m deep",
        ),
        GazeboRun(
            "manual_drop",
            start_x=34.5,
            end_x=38.0,
            camera=Route(3.5, (Stretch(3.5, 4.5, Ground.DROP),)),
            duration_s=20.0,
            says=(Say(0.0, Command.MANUAL),),
            marks=(
                sc.follows_user(),
                sc.stops_before(3.5, "drop"),
                sc.tells("Drop ahead, stopping"),
            ),
            note="in Gazebo nobody drives in manual mode: the speed stays at the last limit",
        ),
        GazeboRun(
            "picture",
            start_x=-1.0,
            end_x=8.0,
            camera=Route(
                9.0,
                (
                    Stretch(
                        5.0,
                        5.1,
                        Ground.STAIRS_UP,
                        seen_by=frozenset({Source.CAMERA}),
                        real=False,
                    ),
                ),
            ),
            duration_s=40.0,
            marks=(
                sc.no_tracks(),
                sc.slower_on(0.0, 5.1, "way to the picture"),
                sc.reaches_end(9.0),
            ),
            note="the picture is only in what the camera stand-in reports; nothing is in the world",
        ),
        GazeboRun(
            "dark",
            start_x=-1.0,
            end_x=7.0,
            camera=Route(8.0, obstacles=(Obstacle(8.0, "person"),)),
            duration_s=20.0,
            boxes=(PERSON,),
            faults=(FaultStep("camera", "dark", True, 0.05, at_s=0.0),),
            marks=(sc.stops_before(8.0, "person"), sc.tells("Obstacle ahead, stopping")),
            note="the person is a box 1.7 m high at 7 m; the camera is darkened by a fault",
        ),
        GazeboRun(
            "imu_lost",
            start_x=18.2,
            end_x=25.0,
            camera=Route(6.8, (CAM_STAIRS,)),
            duration_s=90.0,
            faults=(FaultStep("imu", "dropout", True, at_x=4.0),),
            marks=(
                sc.tracks_before(STAIRS_AT),
                sc.tracks_from(STAIRS_TOP + 0.1),
                sc.tells("On tracks until the ground is level", aloud=False),
            ),
        ),
    )
}


# ---- mishaps


def mishaps_at(x_m: float, z_m: float, mode: Mode, start_x: float, boxes) -> list[str]:
    """What is wrong with a chair at x_m metres from start_x and z_m high, asking for this mode:
    on a step on wheels, over the drop, or at an obstacle. Same rules as World.move."""
    x = start_x + x_m
    found = []
    for what, begin, end in STEP_ZONES:
        if begin <= x < end and mode == Mode.WHEELS:
            found.append(f"on the {what} on wheels at {x_m:.2f} m")
    if x >= DROP_X or z_m < FELL_Z:
        found.append(f"went over the drop at {DROP_X - start_x:.1f} m")
    for box in boxes:
        if x >= box.x:
            found.append(f"hit the {box.name} at {box.x - start_x:.1f} m")
    return found


# ---- the world file of a run


def world_for(sdf: str, run: GazeboRun) -> str:
    """The route's world file with the chair at the start of the run and the boxes of the run."""
    marker = "<pose>1 0 0.38 0 0 0</pose>"
    if sdf.count(marker) != 1:
        raise ValueError("expected one chair pose in the world file")
    out = sdf.replace(marker, f"<pose>{run.start_x:g} 0 0.38 0 0 0</pose>")
    boxes = "".join(_box_sdf(b) for b in run.boxes)
    if boxes:
        out = out.replace("  </world>", boxes + "  </world>")
    return out


def _box_sdf(b: Box) -> str:
    size = " ".join(f"{v:g}" for v in b.size)
    color = " ".join(str(v) for v in b.color)
    shape = f"<geometry><box><size>{size}</size></box></geometry>"
    material = f"<material><ambient>{color} 1</ambient><diffuse>{color} 1</diffuse></material>"
    return (
        f'    <model name="{b.name}"><static>true</static>\n'
        f"      <pose>{b.x:g} {b.y:g} {b.z:g} 0 0 0</pose>\n"
        f'      <link name="link">\n'
        f'        <collision name="c">{shape}</collision>\n'
        f'        <visual name="v">{shape}{material}</visual>\n'
        f"      </link></model>\n"
    )


# ---- traces on disk


def trace_to_json(trace: Trace) -> str:
    return json.dumps(
        {
            "samples": [
                [s.t, s.x_m, int(s.mode), s.stop, s.speed_limit, s.reason] for s in trace.samples
            ],
            "messages": [[m.t, m.text, m.speak] for m in trace.messages],
            "heard": [[t, h.text, int(h.priority), h.speak, h.stamp] for t, h in trace.heard],
            "said": [[t, int(c)] for t, c in trace.said],
            "mishaps": trace.mishaps,
            "end_m": trace.end_m,
        }
    )


def trace_from_json(text: str) -> Trace:
    d = json.loads(text)
    return Trace(
        samples=[
            Sample(t, x, Mode(m), bool(stop), v, why) for t, x, m, stop, v, why in d["samples"]
        ],
        messages=[Message(t, text, bool(speak)) for t, text, speak in d["messages"]],
        heard=[
            (t, Heard(text, Priority(p), bool(sp), stamp)) for t, text, p, sp, stamp in d["heard"]
        ],
        said=[(t, Command(c)) for t, c in d["said"]],
        mishaps=list(d["mishaps"]),
        end_m=d["end_m"],
    )


@dataclass
class Recording:
    """What the scenario runner collects while a run is on, before it is a Trace."""

    trace: Trace = field(default_factory=Trace)
    seen_mishaps: set = field(default_factory=set)

    def add_mishaps(self, found: list[str]) -> None:
        for what in found:
            key = what.split(" at ")[0]
            if key not in self.seen_mishaps:
                self.seen_mishaps.add(key)
                self.trace.mishaps.append(what)


def finite(x: float) -> float:
    return x if math.isfinite(x) else 0.0


# ---- the camera stand-in

CAMERA_RANGE_M = 5.0  # as RANGE_M of the scripted world
CAMERA_TRUST = 0.9
LIT_MEAN = 60.0  # mean pixel value of a lit scene; the camera is trusted less in the dark


def camera_ground(route: Route, x_m: float) -> Ground:
    """What the camera stand-in says about the ground ahead, as the scripted camera does: it names
    the ground but gives no distance. The ground classifier of the real camera is still to be
    trained; until then the stand-in lets a scenario test the rules that need two sensors."""
    ahead = [
        s
        for s in route.stretches
        if Source.CAMERA in s.seen_by and s.end_m > x_m and s.start_m - x_m <= CAMERA_RANGE_M
    ]
    return min(ahead, key=lambda s: s.start_m).ground if ahead else Ground.FLAT


def camera_trust(mean_pixel: float) -> float:
    """The trust of the camera stand-in: full in a lit scene, falling with the light."""
    return CAMERA_TRUST * max(0.0, min(1.0, mean_pixel / LIT_MEAN))
