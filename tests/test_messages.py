"""The message queue and the step directions: what the user hears, in what order."""

import math

import pytest
from mana_core.decision import (
    DecisionCore,
    Ground,
    GroundReport,
    ObstacleReport,
    Priority,
    Source,
)
from mana_core.messages import DECISION, DIRECTION, Message, MessageQueue, direction, metres

INFO, WARNING, URGENT = Priority.INFO, Priority.WARNING, Priority.URGENT


def say(text, priority=WARNING, stamp=0.0, speak=True, kind=DECISION):
    return Message(text, priority, speak, stamp, kind)


def heard(queue, until_s, step_s=0.1):
    """Run the queue and list (time, text) of what it let out."""
    out, t = [], 0.0
    while t <= until_s:
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
    q.push(say("Level ground", INFO, 0.0), 0.0)
    q.push(say("Obstacle ahead, slowing down", WARNING, 0.1), 0.1)
    assert [t for _, t in heard(q, 10)] == ["Obstacle ahead, slowing down", "Level ground"]


def test_the_same_priority_goes_oldest_first():
    q = MessageQueue()
    q.push(say("first", WARNING, 0.0), 0.0)
    q.push(say("second", WARNING, 0.5), 0.5)
    q.push(say("third", WARNING, 1.0), 1.0)
    assert [t for _, t in heard(q, 12)] == ["first", "second", "third"]


def test_a_message_waits_until_the_one_before_it_has_been_said():
    q = MessageQueue(gap_s=1.5, chars_per_s=14.0)
    long_text = "Stairs ahead, changing to tracks"  # 32 characters, about 2.3 s
    q.push(say(long_text, WARNING, 0.0), 0.0)
    q.push(say("Ramp ahead, slowing down", WARNING, 0.0), 0.0)
    out = heard(q, 8)
    assert out[0] == (0.0, long_text)
    assert out[1][1] == "Ramp ahead, slowing down"
    assert out[1][0] >= len(long_text) / 14.0


def test_a_message_that_is_not_spoken_only_waits_the_gap():
    q = MessageQueue(gap_s=1.5)
    q.push(say("Level ground", INFO, 0.0, speak=False), 0.0)
    q.push(say("Ramp ahead", WARNING, 0.0), 0.0)
    out = heard(q, 5)
    assert out[1][0] == 1.5


def test_an_urgent_message_does_not_wait_and_replaces_the_less_urgent_ones():
    q = MessageQueue()
    q.push(say("Stairs ahead, changing to tracks", WARNING, 0.0), 0.0)
    assert q.release(0.0)  # being said now
    q.push(say("Level ground", INFO, 0.2), 0.2)
    q.push(say("Obstacle ahead, slowing down", WARNING, 0.3), 0.3)
    q.push(say("Stopped by you", URGENT, 0.4), 0.4)
    assert q.release(0.4).text == "Stopped by you"  # at once, not after the gap
    assert q.waiting == []  # the INFO and the WARNING were replaced


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
    q.push(say("Drop ahead. Stop.", URGENT, 1.0, kind=DIRECTION), 1.0)
    assert q.release(2.0) is None
    q.push(say("Drop ahead. Stop.", URGENT, 9.0, kind=DIRECTION), 9.0)  # much later
    assert q.release(9.0).text == "Drop ahead. Stop."


def test_an_old_message_is_dropped_not_said_late():
    q = MessageQueue(max_age_s=6.0)
    q.push(say("Level ground", INFO, 0.0), 0.0)
    assert q.release(6.5) is None
    assert q.waiting == []


def test_the_same_text_waiting_twice_is_kept_once():
    q = MessageQueue()
    q.push(say("Ramp ahead, slowing down", WARNING, 0.0), 0.0)
    q.push(say("Ramp ahead, slowing down", WARNING, 0.1), 0.1)
    assert len(q.waiting) == 1


def test_a_decision_is_not_said_twice_in_a_row_within_the_age_limit():
    q = MessageQueue(max_age_s=6.0)
    q.push(say("Ramp ahead, slowing down", WARNING, 0.0), 0.0)
    assert q.release(0.0)
    q.push(say("Ramp ahead, slowing down", WARNING, 3.0), 3.0)
    assert q.release(3.0) is None
    q.push(say("Ramp ahead, slowing down", WARNING, 9.0), 9.0)  # a new situation, long after
    assert q.release(9.0).text == "Ramp ahead, slowing down"


def test_a_decision_that_changed_in_between_is_said_again():
    q = MessageQueue()
    q.push(say("Ramp ahead, slowing down", WARNING, 0.0), 0.0)
    q.release(0.0)
    q.push(say("Level ground", INFO, 2.0), 2.0)
    q.release(2.0)
    q.push(say("Ramp ahead, slowing down", WARNING, 4.0), 4.0)
    assert q.release(4.0).text == "Ramp ahead, slowing down"


def test_a_direction_is_said_again_only_after_it_has_changed():
    q = MessageQueue()
    q.push(say("Steps up ahead, 2 metres.", WARNING, 0.0, kind=DIRECTION), 0.0)
    assert q.release(0.0)
    q.push(say("Steps up ahead, 2 metres.", WARNING, 20.0, kind=DIRECTION), 20.0)
    assert q.release(20.0) is None  # still the same direction, however long it lasts
    q.push(say("Steps up ahead, 1 metre.", WARNING, 21.0, kind=DIRECTION), 21.0)
    assert q.release(21.0).text == "Steps up ahead, 1 metre."


# ---- distances


@pytest.mark.parametrize(
    "d, text",
    [
        (1.97, "2 metres"),
        (1.0, "1 metre"),
        (1.2, "1 metre"),
        (1.6, "2 metres"),
        (0.4, "less than a metre"),
    ],
)
def test_distances_are_said_in_whole_metres(d, text):
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


def obstacle(distance, label="person", trust=0.9, stamp=1.0):
    return ObstacleReport(stamp, trust, Source.LIDAR, distance, label)


def test_nothing_ahead_says_nothing():
    assert direction(core_with(), 1.0) is None
    assert direction(core_with(ground=[ground(Ground.FLAT, math.nan)]), 1.0) is None


def test_steps_up_ahead():
    m = direction(core_with(ground=[ground(Ground.STAIRS_UP, 1.97)]), 1.0)
    assert (m.text, m.priority, m.kind, m.speak) == (
        "Steps up ahead, 2 metres.",
        WARNING,
        DIRECTION,
        True,
    )


def test_steps_down_say_stop():
    m = direction(core_with(ground=[ground(Ground.STAIRS_DOWN, 1.0)]), 1.0)
    assert m.text == "Steps down ahead, 1 metre. Stop."


def test_a_curb_and_a_ramp():
    assert (
        direction(core_with(ground=[ground(Ground.CURB, 1.5)]), 1.0).text == "Curb ahead, 2 metres."
    )
    assert (
        direction(core_with(ground=[ground(Ground.RAMP, 2.0)]), 1.0).text == "Ramp ahead, 2 metres."
    )


def test_a_drop_at_the_edge_is_urgent():
    m = direction(core_with(ground=[ground(Ground.DROP, 0.4)]), 1.0)
    assert (m.text, m.priority) == ("Drop ahead, less than a metre. Stop.", URGENT)
    far = direction(core_with(ground=[ground(Ground.DROP, 1.8)]), 1.0)
    assert (far.text, far.priority) == ("Drop ahead, 2 metres. Stop.", WARNING)


def test_a_step_seen_without_a_distance_is_still_told():
    m = direction(core_with(ground=[ground(Ground.STAIRS_UP, math.nan, Source.CAMERA)]), 1.0)
    assert m.text == "Steps up ahead."


def test_a_step_far_away_is_not_told_yet():
    assert direction(core_with(ground=[ground(Ground.STAIRS_UP, 3.5)]), 1.0) is None


def test_an_obstacle_is_told_by_name_and_distance():
    m = direction(core_with(obstacles=[obstacle(1.2)]), 1.0)
    assert (m.text, m.priority) == ("Person ahead, 1 metre.", WARNING)
    m = direction(core_with(obstacles=[obstacle(1.6, label="")]), 1.0)
    assert m.text == "Obstacle ahead, 2 metres."
    assert direction(core_with(obstacles=[obstacle(2.6)]), 1.0) is None


def test_an_obstacle_at_stopping_distance_is_urgent():
    m = direction(core_with(obstacles=[obstacle(0.7)]), 1.0)
    assert (m.text, m.priority) == ("Stop. Person ahead, less than a metre.", URGENT)


def test_the_most_urgent_hazard_wins_then_the_nearest():
    core = core_with(
        ground=[ground(Ground.STAIRS_UP, 1.0), ground(Ground.DROP, 0.4)],
        obstacles=[obstacle(1.5)],
    )
    assert direction(core, 1.0).text.startswith("Drop ahead")
    core = core_with(ground=[ground(Ground.STAIRS_UP, 1.8)], obstacles=[obstacle(1.0)])
    assert direction(core, 1.0).text == "Person ahead, 1 metre."


def test_reports_that_are_old_or_not_trusted_are_not_told():
    old = ground(Ground.STAIRS_UP, 1.0, stamp=0.0)  # 1 s old, the limit is 0.5 s
    assert direction(core_with(ground=[old]), 1.0) is None
    doubtful = ground(Ground.STAIRS_UP, 1.0, trust=0.3)
    assert direction(core_with(ground=[doubtful]), 1.0) is None
    assert direction(core_with(obstacles=[obstacle(1.0, trust=0.2)]), 1.0) is None
