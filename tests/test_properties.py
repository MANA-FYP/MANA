"""Property-based tests (#83): MANA's safety rules hold for any stream of reports and commands,
not only for the scripted scenarios. Hypothesis draws the streams; derandomize keeps CI repeatable.

Each property is a rule from the design, checked at every tick against what the core was sent:
- a drop within edge_m, from any one trusted and fresh report, stops the chair;
- stairs down within edge_m stop it too, unless the user said yes to them;
- an obstacle within obstacle_stop_m stops it;
- a user STOP holds, at any trust, until a trusted GO;
- MANA goes down stairs only after a trusted yes that no NO or STOP has taken back;
- one sensor alone never changes the mode to tracks;
- stale or untrusted reports never change a decision;
and for the message queue: a stop is always said, and an older decision is never said after a
newer one.
"""

import math

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from mana_core.decision import (
    ASK_DOWN,
    Command,
    DecisionCore,
    Ground,
    GroundReport,
    Limits,
    Mode,
    MotionReport,
    ObstacleReport,
    Priority,
    Source,
)
from mana_core.messages import DECISION, DIRECTION, Message, MessageQueue

LIM = Limits()
DT = 0.1
T0 = 100.0
PROPERTY = settings(
    max_examples=300, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow]
)

distances = st.one_of(st.just(math.nan), st.floats(0.0, 6.0))
trusts = st.floats(0.0, 1.0)
ages = st.floats(0.0, 1.0)  # how old a report is when it arrives; max_age_s is 0.5
ground = st.tuples(
    st.sampled_from([Source.CAMERA, Source.DEPTH_CAMERA, Source.CLASSIFIER]),
    st.sampled_from(list(Ground)),
    distances,
    trusts,
    ages,
)
obstacle = st.tuples(
    st.sampled_from([Source.LIDAR, Source.DETECTOR, Source.DEPTH_CAMERA]),
    st.floats(0.0, 6.0),
    trusts,
    ages,
)
motion = st.tuples(st.floats(-0.6, 0.6), st.booleans(), trusts)
command = st.tuples(st.sampled_from(list(Command)), trusts)
tick = st.tuples(
    st.lists(ground, max_size=3),
    st.lists(obstacle, max_size=2),
    st.one_of(st.none(), motion),
    st.lists(command, max_size=2),
)
streams = st.lists(tick, min_size=1, max_size=60)


def fresh(report, now: float) -> bool:
    return now - report.stamp <= LIM.max_age_s and report.trust >= LIM.min_trust


def feed(c: DecisionCore, step, now: float) -> None:
    grounds, obstacles, mot, _ = step
    for source, kind, distance, trust, age in grounds:
        c.on_ground(GroundReport(now - age, trust, source, kind, distance))
    for source, distance, trust, age in obstacles:
        c.on_obstacle(ObstacleReport(now - age, trust, source, distance))
    if mot is not None:
        pitch, slip, trust = mot
        c.on_motion(MotionReport(now, trust, pitch_rad=pitch, wheel_slip=slip))


@PROPERTY
@given(streams)
def test_the_safety_rules_hold_at_every_tick(stream):
    c = DecisionCore()
    stopped = False  # a STOP the user has not taken back with a trusted GO
    yes_holds = False  # a trusted yes to stairs down that no NO or STOP took back
    asked_at = -math.inf  # when the core last asked about stairs down
    for i, step in enumerate(stream):
        now = T0 + i * DT
        feed(c, step, now)
        for cmd, trust in step[3]:
            asking = c.now - asked_at <= LIM.answer_s  # asked now, or just before
            c.on_command(cmd, trust)
            if cmd == Command.STOP:
                stopped, yes_holds = True, False
            elif cmd == Command.NO:
                yes_holds = False
            elif cmd == Command.GO and trust >= LIM.min_trust:
                stopped = False
            elif cmd == Command.YES and trust >= LIM.min_trust and asking:
                yes_holds = True
        d = c.tick(now)
        if d.reason == ASK_DOWN:
            asked_at = now
        grounds = [r for r in c.ground.values() if fresh(r, now)]
        near = [r for r in c.obstacles.values() if fresh(r, now)]

        assert 0.0 <= d.speed_limit <= 1.0
        if d.stop:
            assert d.speed_limit == 0.0
        if any(r.ground == Ground.DROP and r.distance_m <= LIM.edge_m for r in grounds):
            assert d.stop, "a drop at the edge did not stop the chair"
        if not c.down_ok and any(
            r.ground == Ground.STAIRS_DOWN and r.distance_m <= LIM.edge_m for r in grounds
        ):
            assert d.stop, "stairs down at the edge, with no yes, did not stop the chair"
        if any(r.distance_m <= LIM.obstacle_stop_m for r in near):
            assert d.stop, "an obstacle at stopping distance did not stop the chair"
        if stopped:
            assert d.stop, "a user STOP did not hold until GO"
        if c.down_ok:
            assert yes_holds, "MANA would go down stairs without a yes that still holds"


single_source_ground = st.tuples(st.sampled_from(list(Ground)), distances, trusts, ages)


@PROPERTY
@given(
    st.sampled_from([Source.CAMERA, Source.DEPTH_CAMERA, Source.CLASSIFIER]),
    st.lists(st.lists(single_source_ground, max_size=2), min_size=1, max_size=60),
)
def test_one_sensor_alone_never_changes_the_mode_to_tracks(source, stream):
    """Level ground under the chair, no slip and no user command: only the ground reports could
    ask for tracks, and they all come from one sensor."""
    c = DecisionCore()
    for i, grounds in enumerate(stream):
        now = T0 + i * DT
        for kind, distance, trust, age in grounds:
            c.on_ground(GroundReport(now - age, trust, source, kind, distance))
        c.on_motion(MotionReport(now, 0.9))
        assert c.tick(now).mode == Mode.WHEELS


@PROPERTY
@given(streams, st.lists(st.tuples(ground, st.booleans()), max_size=20))
def test_stale_or_untrusted_reports_never_change_a_decision(stream, extras):
    """Two cores get the same stream; one also gets reports that are too old or not trusted
    enough, from a source the stream does not use (the lidar, for obstacles, and the detector,
    for the ground), so they cannot replace a good report."""
    a, b = DecisionCore(), DecisionCore()
    for i, step in enumerate(stream):
        now = T0 + i * DT
        clean = ([g for g in step[0] if g[0] != Source.CLASSIFIER], [], step[2], step[3])
        for c in (a, b):
            feed(c, clean, now)
            for cmd, trust in step[3]:
                c.on_command(cmd, trust)
        if i < len(extras):
            (_, kind, distance, trust, _), stale = extras[i]
            if stale:
                stamp, trust = now - LIM.max_age_s - 0.01, max(trust, LIM.min_trust)
            else:
                stamp, trust = now, min(trust, LIM.min_trust - 0.01)
            b.on_ground(GroundReport(stamp, trust, Source.CLASSIFIER, kind, distance))
            b.on_obstacle(ObstacleReport(stamp, trust, Source.LIDAR, 0.1))
        assert a.tick(now) == b.tick(now)


# ---- the message queue

pushed = st.tuples(
    st.sampled_from(["Drop ahead, stopping", "Stopped by you", "Path clear", "Ramp ahead"]),
    st.sampled_from(list(Priority)),
    st.sampled_from([DECISION, DIRECTION]),
    st.floats(0.0, 0.5),  # time to the next push
)


@PROPERTY
@given(st.lists(pushed, min_size=1, max_size=40))
def test_the_queue_says_every_stop_and_never_an_older_decision_after_a_newer_one(pushes):
    q, said, now, urgent = MessageQueue(), [], 0.0, []
    for text, priority, kind, gap in pushes:
        m = Message(text, priority, True, now, kind)
        q.push(m, now)
        if kind == DECISION and priority == Priority.URGENT:
            urgent.append(m)
        end = now + gap
        while now < end:
            if out := q.release(now):
                said.append((now, out))
            now = round(now + DT, 3)
    for _ in range(600):  # let everything waiting be said
        if out := q.release(now):
            said.append((now, out))
        now = round(now + DT, 3)
    for m in urgent:
        assert any(out.text == m.text and t >= m.stamp for t, out in said), m
    decisions = [out.stamp for _, out in said if out.kind == DECISION]
    assert decisions == sorted(decisions)
