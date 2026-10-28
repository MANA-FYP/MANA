"""The message queue and the step directions: what the user hears, in what order."""

import math

import pytest
from mana_core.decision import (
    Command,
    DecisionCore,
    Ground,
    GroundReport,
    MotionReport,
    ObstacleReport,
    Priority,
    Source,
)
from mana_core.evaluate import run
from mana_core.messages import DECISION, DIRECTION, Message, MessageQueue, direction, metres
from mana_core.scenarios import SCENARIOS

INFO, WARNING, URGENT = Priority.INFO, Priority.WARNING, Priority.URGENT


def say(text, priority=WARNING, stamp=0.0, speak=True, kind=DECISION):
    return Message(text, priority, speak, stamp, kind)


def tell(text, priority=WARNING, stamp=0.0, speak=True):
    """A direction: they do not replace each other, so they show the order of the queue."""
    return say(text, priority, stamp, speak, DIRECTION)


def heard(queue, until_s, step_s=0.1):
    """Run the queue from the time of the last push and list (time, text) of what it let out."""
    out, t = [], max(queue._now, 0.0)
    while t <= until_s:
        m = queue.release(t)
        if m:
            out.append((round(t, 1), m.text))
        t = round(t + step_s, 3)
    return out


def play(queue, events, until_s, step_s=0.1):
    """Push each message at its own time (its stamp), release every tick, list what was said."""
    waiting, out, t = sorted(events, key=lambda e: e.stamp), [], 0.0
    while t <= until_s:
        while waiting and waiting[0].stamp <= t:
            queue.push(waiting.pop(0), t)
        m = queue.release(t)
        if m:
            out.append((round(t, 1), m.text))
        t = round(t + step_s, 3)
    return out


# ---- the queue


def test_one_message_goes_out_at_once():
    q = MessageQueue()
    q.push(say("Stairs ahead, changing to tracks"), 0.0)
    assert q.release(0.0).text == "Stairs ahead, changing to tracks"
    assert q.release(0.1) is None


def test_the_most_urgent_goes_first_not_the_oldest():
    q = MessageQueue()
    q.push(tell("Ramp ahead.", WARNING, 0.0), 0.0)
    q.push(tell("Drop ahead. Stop.", URGENT, 0.1), 0.1)
    # the older warning is replaced by the urgent message, not said after it
    assert [t for _, t in heard(q, 10)] == ["Drop ahead. Stop."]


def test_the_same_priority_is_said_in_order_when_nothing_urgent_is_waiting():
    q = MessageQueue()
    q.push(tell("Ramp ahead.", WARNING, 0.0), 0.0)
    out = heard(q, 3)
    q.push(tell("Curb ahead.", WARNING, 3.0), 3.0)
    out += heard(q, 8)
    assert [t for _, t in out] == ["Ramp ahead.", "Curb ahead."]


def test_the_same_priority_goes_oldest_first():
    q = MessageQueue()
    q.push(tell("first", WARNING, 0.0), 0.0)
    q.push(tell("second", WARNING, 0.5), 0.5)
    q.push(tell("third", WARNING, 1.0), 1.0)
    assert [t for _, t in heard(q, 12)] == ["first", "second", "third"]


def test_a_decision_goes_before_a_direction_of_the_same_priority():
    q = MessageQueue()
    q.push(say("Going down the stairs on tracks", WARNING, 1.0), 1.0)
    q.push(tell("Steps down ahead, 1 metre. Stop.", WARNING, 0.5), 1.0)  # older: pushed every tick
    assert q.release(1.0).text == "Going down the stairs on tracks"


def test_a_message_waits_until_the_one_before_it_has_been_said():
    q = MessageQueue(gap_s=1.5, chars_per_s=14.0)
    long_text = "Stairs ahead, changing to tracks"  # 32 characters, about 2.3 s
    q.push(tell(long_text, WARNING, 0.0), 0.0)
    q.push(tell("Ramp ahead, slowing down", WARNING, 0.0), 0.0)
    out = heard(q, 8)
    assert out[0] == (0.0, long_text)
    assert out[1][1] == "Ramp ahead, slowing down"
    assert out[1][0] >= len(long_text) / 14.0


def test_a_message_that_is_not_spoken_only_waits_the_gap():
    q = MessageQueue(gap_s=1.5)
    q.push(tell("Level ground", WARNING, 0.0, speak=False), 0.0)
    q.push(tell("Ramp ahead", WARNING, 0.0), 0.0)
    assert heard(q, 5)[1][0] == 1.5


def test_an_urgent_message_does_not_wait_and_replaces_the_less_urgent_ones():
    q = MessageQueue()
    q.push(say("Stairs ahead, changing to tracks", WARNING, 0.0), 0.0)
    assert q.release(0.0)  # being said now
    q.push(tell("Ramp ahead.", WARNING, 0.3), 0.3)
    q.push(say("Stopped by you", URGENT, 0.4), 0.4)
    assert q.release(0.4).text == "Stopped by you"  # at once, not after the gap
    assert q.waiting == []  # the waiting warning was replaced


def test_two_urgent_messages_go_oldest_first_and_the_second_waits_for_the_first():
    q = MessageQueue(gap_s=1.5, chars_per_s=14.0)
    q.push(say("Drop ahead, stopping", URGENT, 0.0), 0.0)
    q.push(say("Obstacle ahead, stopping", URGENT, 0.2), 0.2)
    out = heard(q, 5)
    assert [t for _, t in out] == ["Drop ahead, stopping", "Obstacle ahead, stopping"]
    assert out[1][0] >= len("Drop ahead, stopping") / 14.0  # never cuts the first one off


def test_an_urgent_direction_is_dropped_right_after_a_stop_was_said():
    q = MessageQueue(max_age_s=6.0)
    q.push(say("Drop ahead, stopping", URGENT, 0.0), 0.0)
    assert q.release(0.0)
    q.push(tell("Drop ahead. Stop.", URGENT, 1.0), 1.0)
    assert q.release(2.0) is None
    q.push(tell("Drop ahead. Stop.", URGENT, 9.0), 9.0)  # much later
    assert q.release(9.0).text == "Drop ahead. Stop."


def test_a_stop_also_replaces_a_waiting_decision_that_is_less_urgent():
    q = MessageQueue()
    q.push(say("Ramp ahead, slowing down", WARNING, 0.0), 0.0)
    q.push(tell("Drop ahead. Stop.", URGENT, 0.0), 0.0)
    assert q.release(0.0).text == "Drop ahead. Stop."
    assert heard(q, 5) == []


def test_an_urgent_direction_that_stays_the_same_is_not_said_again():
    q = MessageQueue(max_age_s=6.0)
    q.push(tell("Drop ahead. Stop.", URGENT, 0.0), 0.0)
    assert q.release(0.0)
    q.push(tell("Drop ahead. Stop.", URGENT, 20.0), 20.0)  # long after, but nothing changed
    assert q.release(20.0) is None


def test_a_stop_direction_is_not_said_again_while_the_chair_waits_at_the_same_drop():
    # the node pushes the direction every tick and the decision when it changes, both urgent
    q = MessageQueue(max_age_s=6.0)
    q.push(tell("Drop ahead, less than a metre. Stop.", URGENT, 0.0), 0.0)
    q.push(say("Drop ahead, stopping", URGENT, 0.0), 0.0)
    assert q.release(0.0).text == "Drop ahead, stopping"
    q.push(tell("Drop ahead, less than a metre. Stop.", URGENT, 7.0), 7.0)  # still there
    assert heard(q, 9) == []


def test_a_message_exactly_max_age_s_old_is_still_said():
    q = MessageQueue(max_age_s=6.0)
    q.push(say("Level ground", INFO, 0.0), 0.0)
    assert q.release(6.0).text == "Level ground"


def test_a_repeated_decision_does_not_push_out_a_waiting_direction():
    # late in a run, so that the queue's own clock is far from zero
    q = MessageQueue()
    q.push(say("Ramp ahead, slowing down", WARNING, 10.0), 10.0)
    assert q.release(10.0)
    q.push(tell("Ramp ahead, 1 metre.", WARNING, 10.5), 10.5)  # waits for the gap
    q.push(say("Ramp ahead, slowing down", WARNING, 11.0), 11.0)  # the same decision again
    assert [text for _, text in heard(q, 14)] == ["Ramp ahead, 1 metre."]


def test_a_decision_said_just_before_is_not_said_again_after_one_never_said():
    q = MessageQueue()
    q.push(say("Ramp ahead, slowing down", WARNING, 10.0), 10.0)
    assert q.release(10.0)
    q.push(say("Possible step ahead, slowing down", WARNING, 10.5), 10.5)  # waits, never said
    q.push(say("Ramp ahead, slowing down", WARNING, 11.0), 11.0)  # replaces it: back as before
    q.push(tell("Ramp ahead, 1 metre.", WARNING, 11.0), 11.0)
    assert heard(q, 14) == [(11.8, "Ramp ahead, 1 metre.")]  # at once when the first is said


def test_an_old_message_is_dropped_not_said_late():
    q = MessageQueue(max_age_s=6.0)
    q.push(say("Level ground", INFO, 0.0), 0.0)
    assert q.release(6.5) is None
    assert q.waiting == []


def test_an_urgent_message_is_never_dropped_for_its_age():
    q = MessageQueue(max_age_s=6.0)
    q.push(say("Drop ahead, stopping", URGENT, 0.0), 0.0)
    assert q.release(20.0).text == "Drop ahead, stopping"


def test_the_gap_must_be_shorter_than_the_age_limit():
    with pytest.raises(ValueError, match="gap_s must be shorter than max_age_s"):
        MessageQueue(gap_s=6.0, max_age_s=6.0)


def test_the_same_text_waiting_twice_is_kept_once():
    q = MessageQueue()
    q.push(tell("Ramp ahead, slowing down", WARNING, 0.0), 0.0)
    q.push(tell("Ramp ahead, slowing down", WARNING, 0.1), 0.1)
    assert len(q.waiting) == 1


def test_a_decision_is_not_said_twice_in_a_row_within_the_age_limit():
    q = MessageQueue(max_age_s=6.0)
    q.push(say("Ramp ahead, slowing down", WARNING, 0.0), 0.0)
    assert q.release(0.0)
    q.push(say("Ramp ahead, slowing down", WARNING, 3.0), 3.0)
    assert q.release(3.0) is None
    q.push(say("Ramp ahead, slowing down", WARNING, 9.0), 9.0)  # a new situation, long after
    assert q.release(9.0).text == "Ramp ahead, slowing down"


def test_an_urgent_decision_is_said_again_every_time():
    q = MessageQueue(max_age_s=6.0)
    q.push(say("Stopped by you", URGENT, 0.0), 0.0)
    assert q.release(0.0).text == "Stopped by you"
    q.push(say("Stopped by you", URGENT, 2.0), 2.0)  # the user presses STOP again
    assert q.release(4.0).text == "Stopped by you"


def test_a_decision_that_changed_in_between_is_said_again():
    q = MessageQueue()
    q.push(say("Ramp ahead, slowing down", WARNING, 0.0), 0.0)
    q.release(0.0)
    q.push(say("Level ground", INFO, 2.0), 2.0)
    q.release(2.0)
    q.push(say("Ramp ahead, slowing down", WARNING, 4.0), 4.0)
    assert q.release(4.0).text == "Ramp ahead, slowing down"


def test_only_the_latest_decision_is_said_not_an_older_one_after_it():
    q = MessageQueue()
    events = [
        say("Path clear", INFO, 0.0),
        say("Level ground", INFO, 0.5),
        say("Steps ahead, slowing", WARNING, 1.0),
    ]
    out = [t for _, t in play(q, events, 10)]
    assert out == ["Path clear", "Steps ahead, slowing"]  # not "Level ground" after it


def test_a_change_back_to_a_hazard_is_not_hidden():
    # "Level ground" was pushed and then the steps came back before it was said: it is out of date,
    # the steps are still the truth, and they have just been said
    q = MessageQueue()
    events = [
        say("Steps ahead, slowing", WARNING, 0.0),
        say("Level ground", INFO, 0.5),
        say("Steps ahead, slowing", WARNING, 0.6),
    ]
    out = [t for _, t in play(q, events, 12)]
    assert out == ["Steps ahead, slowing"]
    # when "Level ground" was really said in between, the steps are said again after it
    q = MessageQueue()
    events = [
        say("Steps ahead, slowing", WARNING, 0.0),
        say("Level ground", INFO, 2.0),
        say("Steps ahead, slowing", WARNING, 5.0),
    ]
    out = [t for _, t in play(q, events, 12)]
    assert out == ["Steps ahead, slowing", "Level ground", "Steps ahead, slowing"]


def test_a_stop_that_is_waiting_is_still_said_when_a_newer_decision_comes():
    q = MessageQueue(chars_per_s=14.0)
    q.push(say("A long message that takes a while to say", WARNING, 0.0), 0.0)
    assert q.release(0.0)
    q.push(say("Stopped by you", URGENT, 0.1), 0.1)
    q.push(say("Level ground", INFO, 0.2), 0.2)
    assert "Stopped by you" in [m.text for m in q.waiting]
    assert "Stopped by you" in [t for _, t in heard(q, 10)]


def test_a_direction_that_waits_is_dropped_by_a_new_decision_and_may_come_back():
    q = MessageQueue()
    q.push(tell("Ramp ahead.", WARNING, 0.0), 0.0)
    q.push(say("Going down the stairs on tracks", WARNING, 0.5), 0.5)
    assert [m.text for m in q.waiting] == ["Going down the stairs on tracks"]
    q.push(tell("Ramp ahead.", WARNING, 0.6), 0.6)  # still true: the next tick brings it back
    assert [m.text for m in q.waiting][-1] == "Ramp ahead."


def test_a_direction_is_said_again_only_after_it_has_changed_or_gone():
    q = MessageQueue()
    q.push(tell("Steps up ahead, 2 metres.", WARNING, 0.0), 0.0)
    assert q.release(0.0)
    q.push(tell("Steps up ahead, 2 metres.", WARNING, 20.0), 20.0)
    assert q.release(20.0) is None  # still the same direction, however long it lasts
    q.push(tell("Steps up ahead, 1 metre.", WARNING, 21.0), 21.0)
    assert q.release(21.0).text == "Steps up ahead, 1 metre."
    q.direction_gone()  # the user turned away, and then back
    q.push(tell("Steps up ahead, 1 metre.", WARNING, 40.0), 40.0)
    assert q.release(40.0).text == "Steps up ahead, 1 metre."


def test_a_direction_that_expired_in_the_queue_may_be_pushed_again():
    q = MessageQueue(max_age_s=6.0)
    q.push(say("A long message that takes a while to say", WARNING, 0.0), 0.0)
    q.release(0.0)
    q.push(tell("Ramp ahead.", WARNING, 0.1), 0.1)
    assert q.release(7.0) is None  # it waited too long and was dropped
    q.push(tell("Ramp ahead.", WARNING, 7.1), 7.1)
    assert q.release(7.1).text == "Ramp ahead."


def test_a_clock_that_goes_back_starts_the_queue_again():
    q = MessageQueue()
    q.push(say("Stairs ahead", WARNING, 100.0), 100.0)
    assert q.release(100.0)
    q.push(say("Ramp ahead", WARNING, 50.0), 50.0)  # a simulation was reset
    assert q.release(50.0).text == "Ramp ahead"


# ---- distances


@pytest.mark.parametrize(
    "d, text",
    [
        (1.97, "1 metre"),
        (1.0, "1 metre"),
        (1.2, "1 metre"),
        (1.5, "1 metre"),
        (2.0, "2 metres"),
        (3.5, "3 metres"),
        (0.4, "less than a metre"),
    ],
)
def test_distances_are_said_in_whole_metres_rounded_down(d, text):
    assert metres(d) == text


# ---- step directions


def core_with(ground=(), obstacles=(), now=1.0):
    core = DecisionCore()
    core.now = now
    for g in ground:
        core.on_ground(g)
    for o in obstacles:
        core.on_obstacle(o)
    return core


def ground(kind, distance, source=Source.DEPTH_CAMERA, trust=0.9, stamp=1.0):
    return GroundReport(stamp, trust, source, kind, distance)


def step(kind, distance, **kw):
    """A step as the core confirms it: the camera names it, the 3D camera measures it."""
    return [
        ground(kind, math.nan, Source.CAMERA, **kw),
        ground(kind, distance, Source.DEPTH_CAMERA, **kw),
    ]


def obstacle(distance, label="person", trust=0.9, stamp=1.0):
    return ObstacleReport(stamp, trust, Source.LIDAR, distance, label)


def test_nothing_ahead_says_nothing():
    assert direction(core_with(), 1.0) is None
    assert direction(core_with(ground=[ground(Ground.FLAT, math.nan)]), 1.0) is None


def test_steps_up_ahead():
    m = direction(core_with(ground=step(Ground.STAIRS_UP, 1.97)), 1.0)
    assert (m.text, m.priority, m.kind, m.speak) == (
        "Steps up ahead, 1 metre.",
        WARNING,
        DIRECTION,
        True,
    )


def test_steps_down_say_stop_and_are_urgent_at_the_edge():
    m = direction(core_with(ground=step(Ground.STAIRS_DOWN, 1.0)), 1.0)
    assert (m.text, m.priority) == ("Steps down ahead, 1 metre. Stop.", WARNING)
    m = direction(core_with(ground=step(Ground.STAIRS_DOWN, 0.4)), 1.0)
    assert (m.text, m.priority) == ("Steps down ahead, less than a metre. Stop.", URGENT)


def test_no_direction_for_stairs_down_once_the_user_has_said_yes():
    core = core_with(ground=step(Ground.STAIRS_DOWN, 1.0))
    core.down_ok = True  # the decision already says that MANA is going down
    assert direction(core, 1.0) is None


def test_after_a_yes_to_stairs_down_the_next_thing_heard_is_the_descent():
    """The real core, the queue and the directions at 10 Hz, as the node runs them with a cane."""
    core, q, out = DecisionCore(), MessageQueue(), []
    last_reason, t = None, 0.0
    while t < 9.0:
        for source, d in ((Source.CAMERA, math.nan), (Source.DEPTH_CAMERA, 1.0)):
            core.on_ground(GroundReport(t, 0.9, source, Ground.STAIRS_DOWN, d))
        core.on_motion(MotionReport(t, 0.9))
        if abs(t - 3.0) < 1e-6:
            core.on_command(Command.YES, 0.9)  # the user says yes to the question
        d = core.tick(t)
        if d.reason != last_reason:
            q.push(say(d.reason, d.priority, t, kind=DECISION), t)
            last_reason = d.reason
        if ahead := direction(core, t):
            q.push(ahead, t)
        else:
            q.direction_gone()
        if m := q.release(t):
            out.append((round(t, 1), m.text))
        t = round(t + 0.1, 3)
    texts_ = [x for _, x in out]
    descent = texts_.index("Going down the stairs on tracks")
    assert texts_[0] == "Stairs down ahead. Say yes to go down on tracks"
    yes_at = out[descent][0]
    assert yes_at - 3.0 < 1.0  # straight after the yes, not 2.7 s late
    assert not any(x.endswith("Stop.") for _, x in out if _ >= 3.0)  # no "Stop." after the yes


def test_a_curb_and_a_ramp():
    assert direction(core_with(ground=step(Ground.CURB, 1.5)), 1.0).text == "Curb ahead, 1 metre."
    ramp = direction(core_with(ground=[ground(Ground.RAMP, 2.0)]), 1.0)  # one sensor is enough
    assert ramp.text == "Ramp ahead, 2 metres."


def test_a_drop_at_the_edge_is_urgent():
    m = direction(core_with(ground=[ground(Ground.DROP, 0.4)]), 1.0)
    assert (m.text, m.priority) == ("Drop ahead, less than a metre. Stop.", URGENT)
    far = direction(core_with(ground=[ground(Ground.DROP, 1.8)]), 1.0)
    assert (far.text, far.priority) == ("Drop ahead, 1 metre. Stop.", WARNING)


def test_a_step_that_only_one_sensor_sees_is_not_told_as_a_fact():
    # the picture of stairs: the core says "possible step", so the direction says nothing
    one = [ground(Ground.STAIRS_UP, 1.0, Source.DEPTH_CAMERA)]
    assert direction(core_with(ground=one), 1.0) is None
    camera_and_classifier = [
        ground(Ground.STAIRS_UP, math.nan, Source.CAMERA),
        ground(Ground.STAIRS_UP, math.nan, Source.CLASSIFIER),  # both use the camera image
    ]
    assert direction(core_with(ground=camera_and_classifier), 1.0) is None


def test_a_step_far_away_is_not_told_yet():
    assert direction(core_with(ground=step(Ground.STAIRS_UP, 3.5)), 1.0) is None
    assert direction(core_with(ground=[ground(Ground.DROP, 2.5)]), 1.0) is None


def test_a_drop_at_an_unknown_distance_is_told_without_one():
    m = direction(core_with(ground=[ground(Ground.DROP, math.nan)]), 1.0)
    assert (m.text, m.priority) == ("Drop ahead. Stop.", WARNING)


def test_a_far_report_does_not_hide_a_near_one_after_it():
    far_first = [ground(Ground.DROP, 3.0), ground(Ground.RAMP, 1.0, Source.CAMERA)]
    assert direction(core_with(ground=far_first), 1.0).text == "Ramp ahead, 1 metre."
    far, near = obstacle(3.0), ObstacleReport(1.0, 0.9, Source.DETECTOR, 1.0, "chair")
    assert direction(core_with(obstacles=[far, near]), 1.0).text == "Chair ahead, 1 metre."


def test_each_limit_is_reached_at_its_own_value_for_the_directions():
    lim = DecisionCore().limits
    m = direction(core_with(obstacles=[obstacle(lim.obstacle_stop_m)]), 1.0)
    assert m.priority == URGENT
    m = direction(core_with(obstacles=[obstacle(lim.obstacle_slow_m)]), 1.0)
    assert m.priority == WARNING
    m = direction(core_with(ground=[ground(Ground.DROP, lim.edge_m)]), 1.0)
    assert m.priority == URGENT
    at_limits = obstacle(1.2, trust=lim.min_trust, stamp=1.0 - lim.max_age_s)
    assert direction(core_with(obstacles=[at_limits]), 1.0).text == "Person ahead, 1 metre."


def test_an_obstacle_is_told_by_name_and_distance():
    m = direction(core_with(obstacles=[obstacle(1.2)]), 1.0)
    assert (m.text, m.priority) == ("Person ahead, 1 metre.", WARNING)
    m = direction(core_with(obstacles=[obstacle(1.6, label="")]), 1.0)
    assert m.text == "Obstacle ahead, 1 metre."
    assert direction(core_with(obstacles=[obstacle(2.6)]), 1.0) is None


def test_an_obstacle_at_stopping_distance_is_urgent():
    m = direction(core_with(obstacles=[obstacle(0.7)]), 1.0)
    assert (m.text, m.priority) == ("Stop. Person ahead, less than a metre.", URGENT)


def test_the_most_urgent_hazard_wins_then_the_nearest():
    core = core_with(
        ground=[*step(Ground.STAIRS_UP, 1.0), ground(Ground.DROP, 0.4, Source.CAMERA)],
        obstacles=[obstacle(1.5)],
    )
    assert direction(core, 1.0).text.startswith("Drop ahead")
    core = core_with(ground=step(Ground.STAIRS_UP, 1.8), obstacles=[obstacle(1.0)])
    assert direction(core, 1.0).text == "Person ahead, 1 metre."


def test_reports_that_are_old_or_not_trusted_are_not_told():
    stale = step(Ground.STAIRS_UP, 1.0, stamp=0.0)  # 1 s old, the limit is 0.5 s
    assert direction(core_with(ground=stale), 1.0) is None
    assert direction(core_with(ground=step(Ground.STAIRS_UP, 1.0, trust=0.3)), 1.0) is None
    assert direction(core_with(obstacles=[obstacle(1.0, trust=0.2)]), 1.0) is None


# ---- in the scripted scenarios: what the user hears, in what order


def told(name, cane=False):
    return run(SCENARIOS[name], cane=cane).heard


def texts(name, cane=False):
    return [m.text for _, m in told(name, cane)]


def test_stairs_are_told_in_order_and_each_once():
    assert texts("stairs") == [
        "Level ground",
        "Possible step ahead, slowing down",
        "Stairs ahead, changing to tracks",
        "On tracks until the ground is level",
        "Level ground",
    ]


def test_the_stop_is_urgent_and_heard_the_moment_it_is_made():
    ((t, m),) = [(t, m) for t, m in told("stop") if m.text == "Stopped by you"]
    assert m.priority == URGENT and t - m.stamp < 0.2


def test_a_drop_is_slowed_for_then_stopped_for_in_that_order():
    assert texts("drop")[-2:] == ["Drop ahead, slowing down", "Drop ahead, stopping"]
    assert texts("manual_drop")[-1] == "Drop ahead, stopping"


@pytest.mark.parametrize("name", SCENARIOS)
@pytest.mark.parametrize("cane", [False, True])
def test_one_message_at_a_time(name, cane):
    heard_ = told(name, cane)
    for (t0, a), (t1, b) in zip(heard_, heard_[1:], strict=False):
        assert t1 - t0 >= 1.5 or b.priority == URGENT and a.priority < URGENT, (a.text, b.text)


@pytest.mark.parametrize("name", SCENARIOS)
@pytest.mark.parametrize("cane", [False, True])
def test_nothing_old_is_said(name, cane):
    assert all(t - m.stamp <= 6.0 or m.priority == URGENT for t, m in told(name, cane))


@pytest.mark.parametrize("name", ["stairs", "curb", "ramp", "drop", "dark"])
def test_a_cane_is_told_more_than_a_chair_where_there_is_something_ahead(name):
    chair, cane = told(name), told(name, cane=True)
    assert all(m.kind == DECISION for _, m in chair)
    assert any(m.kind == DIRECTION for _, m in cane)


def test_a_cane_is_told_what_is_ahead_as_it_comes_closer():
    said = texts("stairs", cane=True)
    steps = [t for t in said if t.startswith("Steps up ahead")]
    assert steps == ["Steps up ahead, 1 metre.", "Steps up ahead, less than a metre."]
    assert said.index("Stairs ahead, changing to tracks") < said.index("Steps up ahead, 1 metre.")


def test_a_cane_is_told_about_a_curb_a_ramp_a_drop_and_a_person():
    assert "Curb ahead, 1 metre." in texts("curb", cane=True)
    assert "Ramp ahead, 1 metre." in texts("ramp", cane=True)
    assert "Drop ahead, 1 metre. Stop." in texts("drop", cane=True)
    assert "Person ahead, 1 metre." in texts("dark", cane=True)


def test_a_cane_is_not_told_steps_for_the_picture_of_stairs():
    # only the camera saw stairs: the decision says "possible step", and so does the cane
    said = texts("picture", cane=True)
    assert said == texts("picture")
    assert not any(t.startswith("Steps") for t in said)


def test_a_cane_never_hears_the_same_direction_twice_in_a_row():
    for name in SCENARIOS:
        said = [m.text for _, m in told(name, cane=True) if m.kind == DIRECTION]
        assert all(a != b for a, b in zip(said, said[1:], strict=False)), name


def test_a_cane_hears_no_step_direction_for_flat_ground():
    assert all(m.kind == DECISION for _, m in told("level", cane=True))


def test_the_stop_is_never_held_back_by_a_direction():
    for name in SCENARIOS:
        for t, m in told(name, cane=True):
            if m.kind == DECISION and m.priority == URGENT:
                assert t - m.stamp < 0.2, (name, m.text)


def test_a_stop_is_not_lost_behind_a_less_urgent_copy_of_the_same_text():
    # found by the property tests (#83): the copy that was waiting was skipped as said
    # recently, and the stop, dropped as a duplicate of it, was never said
    q = MessageQueue()
    q.push(Message("Drop ahead, stopping", Priority.INFO, True, 0.0), 0.0)
    assert q.release(0.0).priority == Priority.INFO
    q.push(Message("Stopped by you", Priority.INFO, True, 0.5), 0.5)  # so the next is no repeat
    q.push(Message("Drop ahead, stopping", Priority.INFO, True, 0.5), 0.5)
    q.push(Message("Drop ahead, stopping", Priority.URGENT, True, 0.5), 0.5)
    said = []
    for i in range(5, 100):
        if out := q.release(i / 10):
            said.append((out.text, out.priority))
    assert ("Drop ahead, stopping", Priority.URGENT) in said


def test_a_stop_is_not_lost_behind_a_waiting_direction_with_the_same_text():
    # found by the property tests (#83): a waiting urgent direction is dropped when another stop
    # is said, and the decision dropped as its duplicate went with it
    q = MessageQueue()
    q.push(Message("Drop ahead, stopping", Priority.URGENT, True, 0.0, DIRECTION), 0.0)
    q.push(Message("Drop ahead, stopping", Priority.URGENT, True, 0.0), 0.0)
    q.push(Message("Stopped by you", Priority.URGENT, True, 0.0), 0.0)
    said = []
    for i in range(100):
        if out := q.release(i / 10):
            said.append((out.text, out.kind))
    assert ("Drop ahead, stopping", DECISION) in said
