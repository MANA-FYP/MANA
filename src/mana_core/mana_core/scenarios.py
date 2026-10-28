"""Scripted test scenarios for the decision core, each with its pass marks.

The five from the plan (level, ramp, stairs, curb, stop) come first, then edge and failure cases.
The pass marks are the team's targets: no track request on flat ground or ramps, tracks requested
at least 1.5 m before the first step, a stop acted on within 1 s, and obstacles still announced in
the dark. Every scenario must also end without a mishap: on a step on wheels, over a drop or into
an obstacle.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from mana_core.decision import ASK_DOWN, STEP_STOP, Command, Ground, Limits, Mode, Source
from mana_core.world import Fault, Obstacle, Route, Stretch

SLOW = Limits().slow
STOP_WITHIN_S = 1.0
TRACKS_AHEAD_M = 1.5


@dataclass(frozen=True)
class Say:
    at_s: float
    command: Command
    trust: float = 0.9


@dataclass(frozen=True)
class Sample:
    """What the core asked for at time t, with the chair at x_m."""

    t: float
    x_m: float
    mode: Mode
    stop: bool
    speed_limit: float
    reason: str


@dataclass(frozen=True)
class Message:
    t: float
    text: str
    speak: bool


@dataclass
class Trace:
    samples: list[Sample] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    heard: list[tuple[float, object]] = field(default_factory=list)  # (time, messages.Message)
    said: list[tuple[float, Command]] = field(default_factory=list)
    mishaps: list[str] = field(default_factory=list)
    end_m: float = 0.0

    def first(self, test) -> Sample | None:
        return next((s for s in self.samples if test(s)), None)

    def said_at(self, command: Command) -> float | None:
        return next((t for t, c in self.said if c == command), None)


@dataclass(frozen=True)
class Mark:
    text: str
    test: Callable[[Trace], tuple[bool, str]]


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    shows: str
    route: Route
    duration_s: float
    says: tuple[Say, ...] = ()
    marks: tuple[Mark, ...] = ()


# ---- pass marks


def no_tracks() -> Mark:
    def test(tr):
        s = tr.first(lambda s: s.mode == Mode.TRACKS)
        return s is None, "never on tracks" if s is None else f"tracks requested at {s.x_m:.2f} m"

    return Mark("No track request on flat ground or ramps", test)


def tracks_before(step_m: float) -> Mark:
    def test(tr):
        s = tr.first(lambda s: s.mode == Mode.TRACKS)
        if s is None:
            return False, "tracks never requested"
        gap = step_m - s.x_m
        return gap >= TRACKS_AHEAD_M, f"tracks requested {gap:.2f} m before the first step"

    return Mark(f"Track request at least {TRACKS_AHEAD_M} m before the first step", test)


def wheels_after(x_m: float) -> Mark:
    def test(tr):
        s = tr.first(lambda s: s.x_m >= x_m and s.mode == Mode.WHEELS)
        return s is not None, f"back on wheels at {s.x_m:.2f} m" if s else "still on tracks"

    return Mark("Back on wheels once the ground is level", test)


def tracks_from(x_m: float) -> Mark:
    def test(tr):
        after = [s for s in tr.samples if s.x_m >= x_m]
        ok = bool(after) and all(s.mode == Mode.TRACKS for s in after)
        return ok, "stayed on tracks" if ok else "went back to wheels"

    return Mark("Stays on tracks while the tilt cannot be checked", test)


def slower_on(start_m: float, end_m: float, what: str) -> Mark:
    def test(tr):
        fastest = max((s.speed_limit for s in tr.samples if start_m <= s.x_m < end_m), default=None)
        if fastest is None:
            return False, f"never reached the {what}"
        return fastest <= SLOW, f"at most {fastest:.0%} of top speed on the {what}"

    return Mark(f"Slower on the {what}, on wheels", test)


def full_speed(after_s: float = 1.0) -> Mark:
    def test(tr):
        later = [s for s in tr.samples if s.t >= after_s]
        slowest = min((0.0 if s.stop else s.speed_limit for s in later), default=0.0)
        ok = bool(later) and slowest == 1.0 and all(s.mode == Mode.WHEELS for s in later)
        return ok, f"slowest {slowest:.0%} of top speed"

    return Mark("Full speed on wheels", test)


def full_speed_on(start_m: float, end_m: float, what: str) -> Mark:
    def test(tr):
        on = [s for s in tr.samples if start_m <= s.x_m < end_m]
        slowest = min((0.0 if s.stop else s.speed_limit for s in on), default=None)
        if slowest is None:
            return False, f"never reached the {what}"
        return slowest == 1.0, f"slowest {slowest:.0%} of top speed on the {what}"

    return Mark(f"Full speed on the {what}", test)


def stop_within() -> Mark:
    def test(tr):
        sent = tr.said_at(Command.STOP)
        s = tr.first(lambda s: sent is not None and s.t >= sent and s.stop)
        if s is None:
            return False, "never stopped"
        return s.t - sent <= STOP_WITHIN_S, f"stopped {s.t - sent:.2f} s after the command"

    return Mark(f"Stop acted on within {STOP_WITHIN_S:.0f} s", test)


def stopped_until_go() -> Mark:
    def test(tr):
        stop, go = tr.said_at(Command.STOP), tr.said_at(Command.GO)
        held = [s for s in tr.samples if stop is not None and go is not None and stop < s.t < go]
        moving = [s for s in held if s.t - stop > STOP_WITHIN_S and not s.stop]
        return bool(held) and not moving, "held the stop" if not moving else "moved before go"

    return Mark("Stays stopped until the user says go", test)


def moves_after_go() -> Mark:
    def test(tr):
        go = tr.said_at(Command.GO)
        s = tr.first(lambda s: go is not None and s.t >= go and not s.stop)
        if s is None:
            return False, "never moved again"
        return s.t - go <= STOP_WITHIN_S, f"moving {s.t - go:.2f} s after go"

    return Mark(f"Moving again within {STOP_WITHIN_S:.0f} s of go", test)


def stops_before(at_m: float, what: str) -> Mark:
    def test(tr):
        first = tr.first(lambda s: s.stop)
        gap = at_m - tr.end_m  # the closest it came: the chair never goes back
        if tr.mishaps or first is None or gap <= 0 or not tr.samples[-1].stop:
            return False, f"did not stop for the {what}"
        if (on := tr.end_m - first.x_m) > 0:  # the stop was released for a moment
            return False, f"stopped, then moved on {on:.2f} m, to {gap:.2f} m before the {what}"
        return True, f"stopped {gap:.2f} m before the {what}"

    return Mark(f"Stops before the {what}", test)


def tells(text: str, aloud: bool = True) -> Mark:
    def test(tr):
        m = next((m for m in tr.messages if m.text == text and (m.speak or not aloud)), None)
        return m is not None, f"at {m.t:.1f} s" if m else "not told"

    return Mark(f'{"Says" if aloud else "Shows"} "{text}"', test)


def follows_user() -> Mark:
    def test(tr):
        s = tr.first(lambda s: s.reason == "Manual mode")
        return s is not None, f"manual from {s.t:.1f} s" if s else "never in manual mode"

    return Mark("Follows the user in manual mode", test)


def waits_before(at_m: float, command: Command, what: str) -> Mark:
    def test(tr):
        said = tr.said_at(command)
        before = [s for s in tr.samples if said is None or s.t < said]
        furthest = max((s.x_m for s in before), default=0.0)
        return furthest < at_m, f"{at_m - furthest:.2f} m before the {what} until {command.name}"

    return Mark(f"Waits before the {what} until the user says {command.name}", test)


def reaches_end(length_m: float) -> Mark:
    def test(tr):
        return tr.end_m >= length_m, f"reached {tr.end_m:.2f} of {length_m:.0f} m"

    return Mark("Reaches the end of the route", test)


def _no_mishap(tr):
    return not tr.mishaps, tr.mishaps[0] if tr.mishaps else "none"


NO_MISHAP = Mark(
    "No mishap: never on a step on wheels, over a drop or into an obstacle", _no_mishap
)


def check(scenario: Scenario, trace: Trace) -> list[tuple[Mark, bool, str]]:
    return [(m, *m.test(trace)) for m in (*scenario.marks, NO_MISHAP)]


# ---- the scenarios

STAIRS = Stretch(6.0, 8.0, Ground.STAIRS_UP, pitch_rad=0.55, height_m=0.17)
DROP = Stretch(8.0, 9.0, Ground.DROP)

SCENARIOS = {
    s.name: s
    for s in (
        Scenario(
            "level",
            "Level ground",
            "On level ground MANA drives on wheels at full speed.",
            Route(10.0),
            15.0,
            marks=(no_tracks(), full_speed(), reaches_end(10.0)),
        ),
        Scenario(
            "ramp",
            "Ramp",
            "A ramp is driven on wheels, more slowly, and never on tracks.",
            Route(12.0, (Stretch(6.0, 9.0, Ground.RAMP, pitch_rad=0.08),)),
            40.0,
            marks=(no_tracks(), slower_on(6.0, 9.0, "ramp"), reaches_end(12.0)),
        ),
        Scenario(
            "stairs",
            "Stairs",
            "Both cameras confirm the stairs, so MANA changes to tracks before the first step, "
            "and back to wheels once it is level at the top.",
            Route(11.0, (STAIRS,)),
            40.0,
            marks=(tracks_before(6.0), wheels_after(8.0), reaches_end(11.0)),
        ),
        Scenario(
            "curb",
            "Curb",
            "The 3D camera measures a 12 cm curb and the camera agrees, so MANA crosses it on "
            "tracks.",
            Route(9.0, (Stretch(5.0, 5.3, Ground.CURB, pitch_rad=0.3, height_m=0.12),)),
            30.0,
            marks=(tracks_before(5.0), wheels_after(5.3), reaches_end(9.0)),
        ),
        Scenario(
            "stop",
            "Spoken stop",
            "The user says stop while MANA drives at full speed, then says go.",
            Route(20.0),
            7.0,
            says=(Say(2.0, Command.STOP), Say(5.0, Command.GO)),
            marks=(stop_within(), stopped_until_go(), moves_after_go(), tells("Stopped by you")),
        ),
        Scenario(
            "stairs_down",
            "Stairs down",
            "Both cameras see stairs going down. MANA stops at the top and asks; once the user "
            "says yes it goes down on tracks, and back to wheels at the bottom.",
            Route(12.0, (Stretch(6.0, 8.0, Ground.STAIRS_DOWN, pitch_rad=-0.55, height_m=0.17),)),
            60.0,
            says=(Say(12.0, Command.YES),),
            marks=(
                tells(ASK_DOWN),
                waits_before(6.0, Command.YES, "stairs"),
                wheels_after(8.0),
                reaches_end(12.0),
            ),
        ),
        Scenario(
            "drop",
            "Drop ahead (edge case)",
            "A drop such as a platform edge: MANA slows down when the camera sees it and stops "
            "0.5 m before it on the 3D camera's distance.",
            Route(12.0, (DROP,)),
            20.0,
            marks=(stops_before(8.0, "drop"), tells("Drop ahead, stopping")),
        ),
        Scenario(
            "manual_drop",
            "Manual mode at a drop (edge case)",
            "The user drives in manual mode at full speed towards a drop; safety still stops it.",
            Route(12.0, (DROP,)),
            10.0,
            says=(Say(0.0, Command.MANUAL),),
            marks=(follows_user(), stops_before(8.0, "drop"), tells("Drop ahead, stopping")),
        ),
        Scenario(
            "picture",
            "Picture of stairs (edge case)",
            "Only the camera sees stairs, in a picture on a wall. One source is not enough to "
            "change mode, so MANA only slows down until it has passed.",
            Route(
                10.0,
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
            30.0,
            marks=(no_tracks(), slower_on(0.0, 5.1, "way to the picture"), reaches_end(10.0)),
        ),
        Scenario(
            "dark",
            "Dark corridor (failure case)",
            "The camera cannot be trusted in the dark. The lidar still sees a person ahead, so "
            "MANA stops and says why.",
            Route(12.0, obstacles=(Obstacle(8.0, "person"),), faults=(Fault("camera", trust=0.2),)),
            15.0,
            marks=(stops_before(8.0, "person"), tells("Obstacle ahead, stopping")),
        ),
        Scenario(
            "dark_stairs",
            "Stairs in the dark (failure case)",
            "The camera cannot be trusted, so only the 3D camera sees the stairs. One sensor is "
            "not enough to change mode, so MANA stops at the first step instead of rolling onto "
            "it on wheels; the user then chooses tracks to climb.",
            Route(11.0, (STAIRS,), faults=(Fault("camera", trust=0.2),)),
            40.0,
            says=(Say(15.0, Command.TRACKS),),
            marks=(
                tells(STEP_STOP),
                waits_before(6.0, Command.TRACKS, "stairs"),
                reaches_end(11.0),
            ),
        ),
        Scenario(
            "dark_curb",
            "Curb in the dark (failure case)",
            "Only the 3D camera sees the 12 cm curb. MANA stops before it rather than mount it on "
            "wheels; the user then chooses tracks to cross.",
            Route(
                9.0,
                (Stretch(5.0, 5.3, Ground.CURB, pitch_rad=0.3, height_m=0.12),),
                faults=(Fault("camera", trust=0.2),),
            ),
            30.0,
            says=(Say(12.0, Command.TRACKS),),
            marks=(tells(STEP_STOP), waits_before(5.0, Command.TRACKS, "curb"), reaches_end(9.0)),
        ),
        Scenario(
            "imu_lost",
            "IMU lost on the stairs (failure case)",
            "The IMU stops reporting during the climb. Without the tilt MANA cannot confirm "
            "level ground, so it stays on tracks and tells the user.",
            Route(11.0, (STAIRS,), faults=(Fault("imu", from_m=7.0),)),
            40.0,
            marks=(
                tracks_before(6.0),
                tracks_from(8.0),
                tells("On tracks until the ground is level", aloud=False),
            ),
        ),
        Scenario(
            "entry",
            "Building entry (full system)",
            "Up the entrance ramp on GPS, then inside, where GPS is lost. The lidar map still "
            "knows where the user is, so MANA does not slow down for the position.",
            Route(
                14.0,
                (Stretch(4.0, 7.0, Ground.RAMP, pitch_rad=0.08),),
                faults=(Fault("gps", from_m=8.0),),
                full_system=True,
            ),
            40.0,
            marks=(
                no_tracks(),
                slower_on(4.0, 7.0, "entrance ramp"),
                full_speed_on(8.5, 14.0, "way inside, without GPS"),
                reaches_end(14.0),
            ),
        ),
        Scenario(
            "wifi_drop",
            "Wi-Fi drop (full system)",
            "Wi-Fi fades at 4 m and Bluetooth carries the alerts, so MANA keeps its speed. From 8 "
            "to 10 m Bluetooth is out of range too: no alert could be sent, so MANA slows down.",
            Route(
                14.0,
                faults=(Fault("wifi", from_m=4.0), Fault("bluetooth", from_m=8.0, to_m=10.0)),
                full_system=True,
            ),
            40.0,
            # each mark keeps clear of the fault edges, which the repeated runs move up to 0.5 m,
            # and of the tick it takes to react at full speed (0.12 m)
            marks=(
                full_speed_on(4.5, 7.5, "stretch on Bluetooth"),
                slower_on(8.7, 9.3, "stretch with no alert link"),
                tells("No alert link, slowing down"),
                full_speed_on(11.0, 14.0, "stretch with Bluetooth back"),
                reaches_end(14.0),
            ),
        ),
    )
}
