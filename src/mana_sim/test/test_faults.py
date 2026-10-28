"""The sensor faults and their scenarios. No ROS, no Gazebo."""

import math
import random

import numpy as np
import pytest
from mana_sim.fault_scenarios import FAULT_SCENARIOS, Step, due

from mana_sim import faults as f


def test_with_no_fault_on_everything_is_as_it_was():
    x = f.Faults()
    assert not any(x.dropped(s) for s in ("camera", "depth", "lidar", "imu", "gps"))
    assert (x.noise_sigma("gps"), x.light_left(), x.slip_factor()) == (0.0, 1.0, 1.0)
    assert x.wifi_quality(100.0) == 1.0
    assert x.summary() == "none"


def test_a_fault_is_switched_on_and_off():
    x = f.Faults()
    assert x.set("gps", "dropout", True, 0.0, 5.0) == (True, "gps dropout")
    assert x.dropped("gps")
    assert x.set("gps", "dropout", False, 0.0, 9.0) == (True, "none")
    assert not x.dropped("gps")


def test_a_fault_that_makes_no_sense_is_refused():
    x = f.Faults()
    assert not x.set("wifi", "noise", True, 0.0, 0.0)[0]
    assert not x.set("camera", "melt", True, 0.0, 0.0)[0]
    assert not x.set("lidar", "dark", True, 0.0, 0.0)[0]
    assert x.summary() == "none"


def test_the_strength_is_the_default_when_it_is_zero_or_not_a_number():
    x = f.Faults()
    x.set("depth", "noise", True, 0.0, 0.0)
    assert x.noise_sigma("depth") == f.NOISE_SIGMA["depth"]
    x.set("depth", "noise", True, 4.0, 0.0)
    assert x.noise_sigma("depth") == pytest.approx(4 * f.NOISE_SIGMA["depth"])
    x.set("depth", "noise", True, math.nan, 0.0)
    assert x.noise_sigma("depth") == f.NOISE_SIGMA["depth"]


def test_the_wifi_heartbeats_get_lost_more_and_more_over_the_fade_time():
    x = f.Faults()
    x.set("wifi", "fade", True, 10.0, 100.0)
    assert x.wifi_quality(100.0) == 1.0
    assert x.wifi_quality(105.0) == pytest.approx(0.5)
    assert x.wifi_quality(109.0) == pytest.approx(0.1)
    assert x.wifi_quality(110.0) == 0.0
    assert x.wifi_quality(500.0) == 0.0  # and it stays gone
    assert x.wifi_quality(50.0) == 1.0  # before it started
    x.set("wifi", "fade", False, 0.0, 120.0)
    assert x.wifi_quality(130.0) == 1.0


def beats_during_a_fade(seconds: float, fade_s: float):
    """Which Wi-Fi heartbeats (one a second) get through a fade of fade_s that starts at 100 s."""
    x = f.Faults()
    x.set("wifi", "fade", True, fade_s, 100.0)
    return [x.wifi_beat(100.0 + t) for t in range(int(seconds))]


def test_a_fade_is_the_same_pattern_every_time_and_ends_with_no_heartbeats():
    a = beats_during_a_fade(14, 10.0)
    assert a == beats_during_a_fade(14, 10.0)
    assert a[0] and not any(a[10:])  # the first gets through, none after the fade
    lost = [i for i, ok in enumerate(a[:10]) if not ok]
    gaps = [b - a_ for a_, b in zip(lost, lost[1:], strict=False)]
    assert gaps == sorted(gaps, reverse=True)  # lost more and more often


def test_with_no_fade_every_wifi_heartbeat_gets_through():
    x = f.Faults()
    assert all(x.wifi_beat(float(t)) for t in range(50))


def test_the_link_node_leaves_wifi_once_for_a_10_s_fade_and_comes_back_after():
    # the real Heartbeat and LinkSelector of mana_connectivity, fed by the fade, 10 times a second
    link = pytest.importorskip("mana_connectivity.link")
    x = f.Faults()
    beat = link.Heartbeat(1.0)
    bluetooth = link.Heartbeat(1.0)
    selector = link.LinkSelector(2, 5.0)
    x.set("wifi", "fade", True, 10.0, 10.0)
    beat.beat(0.0)
    bluetooth.beat(0.0)
    seen, last = [], None
    for tick in range(0, 400):  # 40 s
        now = tick / 10
        if now == 30.0:
            x.set("wifi", "fade", False, 0.0, now)
        if now >= 10.0 and now == int(now):  # a heartbeat every second
            bluetooth.beat(now)
            if x.wifi_beat(now):
                beat.beat(now)
        elif now < 10.0 and now == int(now):
            beat.beat(now)
            bluetooth.beat(now)
        active = selector.update(beat.missed(now), bluetooth.missed(now), now)
        if active != last:
            seen.append((round(now, 1), active))
            last = active
    away = [a for _, a in seen]
    assert away == [link.Link.WIFI, link.Link.BLUETOOTH, link.Link.WIFI], seen


def test_noise_has_the_size_asked_for_and_leaves_non_numbers_alone():
    rng = random.Random(1)
    out = f.noisy([2.0] * 2000 + [math.inf, math.nan], 0.1, rng)
    body = np.array(out[:2000])
    assert abs(body.mean() - 2.0) < 0.02 and abs(body.std() - 0.1) < 0.02
    assert out[2000] == math.inf and math.isnan(out[2001])
    same = [1.0, 2.0]
    assert f.noisy(same, 0.0, rng) is same


def test_a_noisy_fix_is_a_few_metres_off():
    rng = random.Random(2)
    lat, lon = f.noisy_gps(33.0, 73.0, 3.0, rng)
    off_m = math.hypot(
        (lat - 33.0) / f.DEGREES_PER_METRE,
        (lon - 73.0) / f.DEGREES_PER_METRE * math.cos(math.radians(33.0)),
    )
    assert 0.0 < off_m < 20.0


def test_image_noise_keeps_the_size_and_the_range():
    rng = np.random.default_rng(1)
    grey = bytes([128]) * 3000
    out = f.noisy_image(grey, "rgb8", 20.0, rng)
    assert len(out) == 3000 and out != grey
    assert f.noisy_image(grey, "rgb8", 0.0, rng) is grey
    depth = np.array([1.0, 2.0, np.inf, np.nan], dtype=np.float32).tobytes()
    back = np.frombuffer(f.noisy_image(depth, "32FC1", 0.1, rng), dtype=np.float32)
    assert math.isinf(back[2]) and math.isnan(back[3]) and back[0] != 1.0


def test_darkness_keeps_a_share_of_the_light():
    light = bytes([200]) * 10
    assert f.darkened(light, 1.0) is light
    assert set(f.darkened(light, 0.05)) == {10}
    assert set(f.darkened(light, 0.0)) == {0}


def test_every_scenario_switches_its_fault_on_and_then_off():
    f_check = f.Faults()
    for s in FAULT_SCENARIOS.values():
        assert len(s.steps) == 2 and s.steps[0].active and not s.steps[1].active
        assert s.steps[0].at_s < s.steps[1].at_s
        for step in s.steps:
            ok, why = f_check.set(step.sensor, step.kind, step.active, step.value, 0.0)
            assert ok, (s.name, why)
    assert f_check.summary() == "none"


def test_there_is_a_scenario_for_each_kind_of_fault():
    kinds = {step.kind for s in FAULT_SCENARIOS.values() for step in s.steps}
    assert kinds == set(f.KINDS)


def test_steps_come_due_once_and_in_order():
    steps = (Step(10.0, "gps", "dropout", True), Step(30.0, "gps", "dropout", False))
    assert due(steps, 5.0, 0) == []
    assert due(steps, 10.0, 0) == [steps[0]]
    assert due(steps, 12.0, 1) == []
    assert due(steps, 31.0, 1) == [steps[1]]
    assert due(steps, 99.0, 2) == []
    assert due(steps, 99.0, 0) == list(steps)
