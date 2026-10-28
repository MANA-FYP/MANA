"""The world variant for machines with no GPU. No ROS, no Gazebo."""

from pathlib import Path

import pytest
from mana_sim.world_variants import without_rendering_sensors

WORLD = Path(__file__).parents[1] / "worlds" / "mana_route.sdf"


def test_the_sensors_system_is_taken_out_and_the_rest_stays():
    sdf = WORLD.read_text()
    out = without_rendering_sensors(sdf)
    assert "gz-sim-sensors-system" not in out and "ogre2" not in out
    for kept in (
        "gz-sim-physics-system",
        "gz-sim-imu-system",
        "gz-sim-navsat-system",
        "mana_chair",
    ):
        assert kept in out
    assert out.count("<plugin") == sdf.count("<plugin") - 1


def test_the_result_is_still_one_world():
    out = without_rendering_sensors(WORLD.read_text())
    assert (
        out.count("<world ") == 1 and out.count("</world>") == 1 and out.rstrip().endswith("</sdf>")
    )


def test_a_world_with_no_sensors_system_is_refused():
    with pytest.raises(ValueError):
        without_rendering_sensors("<sdf><world name='w'></world></sdf>")
