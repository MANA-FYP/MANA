"""The supervisor's panel (#80): what it shows and which switches it has. No ROS."""

import math

from mana_core.decision import Command, Ground, Source
from mana_sim.faults import KINDS

from mana_sim import panel as pn


def panel_at(now=10.0):
    p = pn.Panel()
    p.on_decision(now, 0, False, 0.3, "Drop ahead, slowing down")
    p.on_report("ground 1", now - 0.1, 0.9, pn.ground_text(Source.DEPTH_CAMERA, Ground.DROP, 1.8))
    p.on_report("ground 0", now - 2.0, 0.9, pn.ground_text(Source.CAMERA, Ground.FLAT, math.nan))
    p.on_report("obstacle 2", now, 0.2, pn.obstacle_text(Source.LIDAR, "person", 3.0))
    return p


def test_reports_are_split_into_trusted_and_not_used_with_the_cores_limits():
    v = panel_at().view(10.0)
    assert [r["text"] for r in v["trusted"]] == ["Depth camera: drop at 1.8 m"]
    why = {r["text"]: r["why"] for r in v["doubtful"]}
    assert why == {"Camera: flat": "2.0 s old", "Lidar: person at 3.0 m": "trust 0.20"}


def test_the_decision_and_the_driver_are_shown():
    p = panel_at()
    v = p.view(10.0)
    assert v["decision"]["reason"] == "Drop ahead, slowing down"
    assert (v["decision"]["mode"], v["decision"]["speed_limit"]) == ("wheels", 0.3)
    assert v["driver"].startswith("MANA's host")
    p.on_driver("supervisor")
    assert p.view(10.0)["driver"] == "supervisor"


def test_a_position_and_a_link_count_only_while_recent_and_usable():
    p = pn.Panel()
    p.on_position(7.0, 0, 0.9, 1.5)
    p.on_link(9.9, 0, True, 0)
    p.on_link(9.9, 1, True, 3)  # missed 3 heartbeats in a row
    v = p.view(10.0)
    assert v["position"] == {"source": "GPS", "error_m": 1.5, "lost": True}  # 3 s > 2.5 s
    assert v["links"]["Wi-Fi"]["usable"] and not v["links"]["Bluetooth"]["usable"]


def test_the_fault_switches_are_faults_the_simulation_has_and_show_what_is_on():
    for label, sensor, kind in pn.FAULTS:
        assert sensor in KINDS[kind], label
        assert pn.fault(label) == (sensor, kind)
    assert pn.fault("Open the door") is None
    every = {(sensor, kind) for kind, sensors in KINDS.items() for sensor in sensors}
    assert {(s, k) for _, s, k in pn.FAULTS} == every  # every fault of the simulation, once
    assert len(pn.FAULTS) == len(every)
    p = pn.Panel()
    p.on_faults("gps dropout, wifi fade")
    on = {f["label"] for f in p.view(0.0)["switches"]["faults"] if f["on"]}
    assert on == {"Lose GPS", "Fade Wi-Fi"}


def test_the_command_buttons_are_stop_go_yes_and_no():
    assert pn.COMMANDS == {
        "STOP": Command.STOP,
        "GO": Command.GO,
        "YES": Command.YES,
        "NO": Command.NO,
    }


def test_an_unknown_code_is_shown_as_its_number():
    assert pn.ground_text(99, Ground.FLAT, math.nan) == "code 99: flat"
    p = pn.Panel()
    p.on_decision(0.0, 7, True, 0.0, "x")
    assert p.view(0.0)["decision"]["mode"] == "code 7"
