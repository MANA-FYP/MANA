"""The stopping-distance model of tools/stopping_distance.py (#84), against hand calculations."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from stopping_distance import SLOW, TOP_MPS, main, standstill  # noqa: E402


def test_with_no_delay_the_chair_stops_its_braking_distance_inside_the_stop_distance():
    # slowed to 0.36 m/s well before 0.5 m: braking from there takes 0.36^2 / (2 * 3) = 0.0216 m
    left, v = standstill(2.0, 2.0, 0.5, reaction_s=0.0, decel=3.0)
    assert v == pytest.approx(SLOW * TOP_MPS)
    assert left == pytest.approx(0.5 - (SLOW * TOP_MPS) ** 2 / 6.0, abs=0.002)


def test_the_delay_adds_its_distance_at_the_speed_it_is_driven():
    a, b = (standstill(2.0, 2.0, 0.5, reaction_s=r, decel=3.0)[0] for r in (0.0, 0.2))
    assert a - b == pytest.approx(0.2 * SLOW * TOP_MPS, abs=0.002)


def test_a_late_obstacle_at_a_weak_deceleration_is_not_stopped_for():
    left, _ = standstill(1.2, 2.0, 0.8, reaction_s=0.2, decel=0.5)
    assert left < 0.0  # the chair reaches it: the table shows such cases as negative


def test_command_line(capsys):
    assert main(["--reaction-s", "0.2"]) == 0
    assert "| drop: seen at 2 m, stop at 0.5 m |" in capsys.readouterr().out
