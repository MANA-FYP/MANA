"""Faults that come with a place on the campus (#79). No ROS."""

from mana_sim.campus import build
from mana_sim.zones import ZONE_FAULTS, ZoneWatch


def walk(watch, points):
    return [watch.update(x, y) for x, y in points]


def test_going_indoors_loses_gps_and_coming_out_brings_it_back():
    w = ZoneWatch(build().zones)
    assert w.update(12.0, 0.0) == []  # the plaza
    assert w.update(12.0, 7.0) == [("gps", "dropout", True, 1.0, "indoors")]
    assert w.update(12.0, 9.0) == []  # still indoors: nothing new
    assert w.update(12.0, 3.0) == [("gps", "dropout", False, 1.0, "indoors")]


def test_the_dark_room_and_the_hall_switch_their_own_faults():
    w = ZoneWatch(build().zones)
    w.update(12.0, 9.0)  # the lobby
    assert w.update(3.0, 9.5) == [("camera", "dark", True, 0.05, "dark_room")]
    assert w.update(12.0, 9.5) == [("camera", "dark", False, 0.05, "dark_room")]
    assert w.update(12.0, 20.0) == [("wifi", "fade", True, 10.0, "weak_wifi")]
    assert "indoors" in w.inside  # GPS stays lost all the while


def test_standing_in_a_doorway_does_not_switch_on_and_off():
    w = ZoneWatch(build().zones)
    w.update(12.0, 6.05)  # just inside the south door
    jitter = walk(w, [(12.0, 5.9), (12.0, 6.1), (12.0, 5.8), (12.0, 6.0)])
    assert jitter == [[], [], [], []]  # within 0.3 m of the zone: still in
    assert w.update(12.0, 5.6) == [("gps", "dropout", False, 1.0, "indoors")]


def test_every_campus_zone_has_its_faults_and_starting_inside_switches_them():
    zones = build().zones
    assert set(zones) == set(ZONE_FAULTS)
    w = ZoneWatch(zones)
    assert [c[4] for c in w.update(3.0, 10.0)] == ["indoors", "dark_room"]
