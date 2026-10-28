"""Who drives (#77): the supervisor takes the controls from the autopilot and hands them back,
and MANA's drive request caps whichever one drives. No ROS, no Gazebo."""

import pytest
from mana_core.decision import Command
from mana_sim.driver import (
    AUTOPILOT,
    DOWN,
    FORWARD_RANGE,
    FORWARD_STEP,
    LEFT,
    RIGHT,
    SUPERVISOR,
    TURN_MAX,
    TURN_STEP,
    UP,
    WATCHDOG_S,
    DriverSelect,
    Keys,
)
from mana_sim.host import TOP_SPEED, WHEELS, Host


def test_the_autopilot_drives_until_the_supervisor_takes_over():
    s = DriverSelect()
    s.command(AUTOPILOT, 0.8, 0.1, 0.0)
    s.command(SUPERVISOR, 0.2, -0.5, 0.0)
    assert (s.driver, s.output(0.0)) == (AUTOPILOT, (0.8, 0.1))
    s.take_over()
    assert (s.driver, s.output(0.0)) == (SUPERVISOR, (0.2, -0.5))
    s.hand_back()
    assert s.output(0.0) == (0.8, 0.1)


def test_while_the_supervisor_drives_the_autopilot_is_not_followed():
    s = DriverSelect()
    s.take_over()
    s.command(SUPERVISOR, 0.0, 0.0, 1.0)
    s.command(AUTOPILOT, 1.0, 0.0, 1.0)  # still sending, and ignored
    assert s.output(1.0) == (0.0, 0.0)


def test_a_driver_that_goes_quiet_gives_no_motion_and_no_fall_back():
    s = DriverSelect()
    s.take_over()
    s.command(SUPERVISOR, 0.5, 0.0, 0.0)
    s.command(AUTOPILOT, 0.9, 0.0, 10.0)
    assert s.output(WATCHDOG_S) == (0.5, 0.0)
    assert s.output(WATCHDOG_S + 0.1) == (0.0, 0.0)  # not the autopilot's 0.9
    assert s.driver == SUPERVISOR
    assert DriverSelect().output(0.0) == (0.0, 0.0)  # nobody has driven yet


def test_a_take_over_counts_at_the_next_tick_and_a_mana_stop_still_holds():
    """The issue's check: during an autopilot drive, a key takes over at the next control tick,
    and MANA's stop holds for the supervisor too."""
    s, host = DriverSelect(), Host()
    for i in range(10):  # the autopilot drives at 10 Hz
        s.command(AUTOPILOT, 1.0, 0.0, i / 10)
    keys = Keys()
    assert keys.press(UP) == [("take", None)]  # the supervisor presses up at 0.95 s
    s.take_over()
    s.command(SUPERVISOR, keys.forward, keys.turn, 0.95)
    assert s.output(1.0) == (FORWARD_STEP, 0.0)  # the next tick follows the supervisor
    c = host.update(WHEELS, False, 1.0, 1.0, driver=s.output(1.0))
    assert c.wheels_mps == pytest.approx(FORWARD_STEP)
    keys.press(UP)
    keys.press(UP)
    s.command(SUPERVISOR, keys.forward, keys.turn, 1.05)
    c = host.update(WHEELS, True, 1.0, 1.05, driver=s.output(1.05))  # MANA says stop
    assert c.wheels_mps == 0.0
    c = host.update(WHEELS, False, 0.1, 1.1, driver=s.output(1.1))  # and slow
    assert c.wheels_mps == pytest.approx(0.1 * TOP_SPEED)


def test_arrow_keys_step_the_speed_and_the_turn_within_their_ranges():
    k = Keys()
    for _ in range(30):
        k.press(UP)
    assert k.forward == FORWARD_RANGE[1]
    for _ in range(30):
        k.press(DOWN)
    assert k.forward == FORWARD_RANGE[0]
    k.press(LEFT)
    assert k.turn == TURN_STEP
    for _ in range(20):
        k.press(RIGHT)
    assert k.turn == -TURN_MAX


def test_x_stands_still_h_hands_back_and_q_quits():
    k = Keys(forward=0.5, turn=0.4)
    assert k.press("x") == [] and (k.forward, k.turn) == (0.0, 0.0)
    k.forward = 0.5
    assert k.press("h") == [("hand back", None)] and k.forward == 0.0
    assert k.press("q") == [("quit", None)]


@pytest.mark.parametrize(
    ("key", "command"),
    [
        (" ", Command.STOP),
        ("g", Command.GO),
        ("y", Command.YES),
        ("N", Command.NO),  # capitals work too
        ("m", Command.MANUAL),
        ("a", Command.AUTOMATIC),
        ("w", Command.WHEELS),
        ("t", Command.TRACKS),
        ("+", Command.SPEED_UP),
        ("-", Command.SLOW_DOWN),
    ],
)
def test_command_keys_send_the_user_s_commands(key, command):
    assert Keys().press(key) == [("command", command)]


def test_space_also_stops_the_supervisor_s_own_motion_and_other_keys_do_nothing():
    k = Keys(forward=0.6, turn=0.2)
    k.press(" ")
    assert (k.forward, k.turn) == (0.0, 0.0)
    assert Keys().press("z") == []
