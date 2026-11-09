"""Position and path from the phone (#129): steps, compass heading and GPS, on made-up walks
whose answer is known."""

import math
import random

import pytest

from demo.navigation import Fix, Navigator, Step, StepCounter, Track, compass_heading

G = 9.81
RATE = 30.0  # readings a second, as the phone page sends them


def walk(seconds, cadence=1.8, swing=3.0, noise=0.15, seed=1, t0=0.0):
    """Acceleration of a person walking with the phone in hand: one rise and fall per step, on
    an axis that changes as the hand turns (the counter uses the size only)."""
    rnd = random.Random(seed)
    for i in range(int(seconds * RATE)):
        t = t0 + i / RATE
        m = G + swing / 2 * math.sin(2 * math.pi * cadence * t) + rnd.gauss(0, noise)
        tilt = 0.3 * math.sin(0.2 * t)
        yield t, m * math.sin(tilt), 0.0, m * math.cos(tilt)


def still(seconds, noise=0.15, seed=2, t0=0.0):
    rnd = random.Random(seed)
    for i in range(int(seconds * RATE)):
        yield t0 + i / RATE, rnd.gauss(0, noise), rnd.gauss(0, noise), G + rnd.gauss(0, noise)


def count(readings, counter=None):
    counter = counter or StepCounter()
    return [s for r in readings if (s := counter.add(*r)) is not None]


# ---- steps


def test_a_walk_of_20_s_at_1_8_steps_a_second_counts_36_steps():
    steps = count(walk(20.0))
    assert abs(len(steps) - 36) <= 1  # the first one is lost while gravity is found


@pytest.mark.parametrize("cadence", [1.2, 1.6, 2.2])
def test_slow_and_fast_walks_count_every_step(cadence):
    assert abs(len(count(walk(20.0, cadence=cadence))) - 20 * cadence) <= 1


def test_a_phone_held_still_counts_no_steps():
    assert count(still(30.0)) == []


def test_a_gap_in_the_readings_does_not_make_a_step():
    counter = StepCounter()
    count(still(5.0), counter)
    assert count(still(5.0, t0=20.0), counter) == []  # 15 s with nothing, then still again


def test_steps_count_again_at_once_when_the_phone_page_is_reloaded():
    # a reloaded page's clock starts again at 0; the walk before it went on to 60 s
    counter = StepCounter()
    count(walk(60.0), counter)
    again = count(walk(10.0, seed=3), counter)
    assert abs(len(again) - 18) <= 1


def test_step_length_is_longer_for_a_stronger_swing_and_stays_in_bounds():
    soft = count(walk(10.0, swing=2.4))
    hard = count(walk(10.0, swing=6.0))
    mean = lambda steps: sum(s.length_m for s in steps) / len(steps)  # noqa: E731
    assert 0.3 <= mean(soft) < mean(hard) <= 1.0


def test_a_walk_of_known_length_sets_the_step_length():
    counter = StepCounter()
    steps = count(walk(20.0), counter)
    counter.calibrate(steps, walked_m=25.0)
    again = count(walk(20.0, seed=3), StepCounter(k=counter.k))
    assert sum(s.length_m for s in again) == pytest.approx(25.0, rel=0.05)


# ---- heading


@pytest.mark.parametrize(
    "alpha, beta, gamma, heading",
    [
        (0, 0, 0, 0),  # flat, top edge to the north
        (90, 0, 0, 270),  # flat, turned left: alpha counts anticlockwise
        (270, 0, 0, 90),
        (0, 90, 0, 0),  # upright, the back camera looks north
        (90, 90, 0, 270),
        (180, 80, 0, 180),
        (0, 60, 0, 0),  # tilted down at the floor ahead, as for the camera
    ],
)
def test_the_compass_heading_from_the_phone_orientation(alpha, beta, gamma, heading):
    got = compass_heading(alpha, beta, gamma)
    assert min(abs(got - heading), 360 - abs(got - heading)) < 0.5


# ---- the track


def walk_legs(nav, legs, step_m=0.7, t0=0.0, every=0.55):
    """Walk straight legs of (heading, steps); returns the time at the end."""
    t = t0
    for heading, n in legs:
        nav.on_orientation(None, None, None, compass=heading)
        for _ in range(n):
            t += every
            nav.track.on_step(Step(t, step_m), t)
    return t


def test_a_square_walked_on_steps_ends_where_it_started():
    nav = Navigator()
    walk_legs(nav, [(0, 20), (90, 20), (180, 20), (270, 20)])
    v = nav.view(50.0)
    assert math.hypot(v["x"], v["y"]) < 0.01
    assert v["steps"] == 80 and v["distance_m"] == pytest.approx(56.0, abs=0.1)
    assert v["source"] == "steps" and v["lat"] is None  # no GPS yet, so no latitude
    xs = [p[0] for p in v["path"]]
    assert max(xs) == pytest.approx(14.0, abs=0.1)  # 20 steps of 0.7 m to the east


def test_steps_with_no_heading_yet_are_counted_but_do_not_move():
    track = Track()
    track.on_step(Step(1.0, 0.7), 1.0)
    assert (track.steps, track.x, track.y) == (1, 0.0, 0.0)


def fix_at(track, e, n, t, acc=5.0, origin=(31.5204, 74.3587)):
    """A GPS fix e metres east and n metres north of origin."""
    lat = origin[0] + math.degrees(n / 6_371_000.0)
    lon = origin[1] + math.degrees(e / (6_371_000.0 * math.cos(math.radians(origin[0]))))
    return Fix(t, lat, lon, acc)


def test_the_first_good_fix_ties_the_track_to_the_earth_where_the_person_is():
    nav = Navigator()
    walk_legs(nav, [(90, 10)])  # 7 m east before GPS
    fix = fix_at(nav.track, 0, 0, 10.0)
    assert nav.on_gps(fix)
    lat, lon = nav.track.latlon()
    assert (lat, lon) == pytest.approx((fix.lat, fix.lon), abs=1e-7)
    assert nav.track.x == pytest.approx(7.0)  # the path so far is kept


def test_outdoors_the_track_follows_gps_and_indoors_it_carries_on_from_steps():
    nav = Navigator()
    origin = (31.5204, 74.3587)
    nav.on_gps(Fix(0.0, *origin, 4.0))
    # outdoors: walk north with GPS agreeing
    t = 0.0
    nav.on_orientation(None, None, None, compass=0)
    for i in range(1, 21):
        t = i * 0.55
        nav.track.on_step(Step(t, 0.7), t)
        if i % 2 == 0:
            assert nav.on_gps(fix_at(nav.track, 0.0, 0.7 * i, t, 4.0))
    assert nav.view(t)["source"] == "gps"
    assert nav.track.y == pytest.approx(14.0, abs=0.5)
    # into the building: GPS goes poor, so it is not used, and the steps carry on east
    assert not nav.on_gps(fix_at(nav.track, 30.0, 30.0, t + 0.5, 60.0))
    walk_legs(nav, [(90, 10)], t0=t + 4.0)
    v = nav.view(t + 10.0)
    assert v["source"] == "steps"
    assert (v["x"], v["y"]) == pytest.approx((7.0, 14.0), abs=0.5)
    assert v["distance_m"] == pytest.approx(21.0, abs=1.0)


def test_the_view_says_where_the_start_is_once_gps_has_said_it():
    track = Track()
    track.on_step(Step(0.5, 0.7), 0.5)
    assert track.view(1.0)["origin"] is None  # steps alone: the map has no anchor
    track.on_gps(Fix(1.0, 31.5204, 74.3587, 4.0))
    lat, lon = track.view(1.0)["origin"]
    # the first fix is where the person is now, so the start is that, less the steps walked
    assert (lat, lon) == pytest.approx(track._shift(31.5204, 74.3587, -track.x, -track.y))


def test_one_fix_far_off_is_refused_but_several_that_agree_move_the_track():
    track = Track()
    track.on_gps(fix_at(track, 0, 0, 0.0, 3.0))
    assert not track.on_gps(fix_at(track, 80, 0, 1.0, 3.0))  # 80 m in a second: a jump
    assert track.x == pytest.approx(0.0, abs=0.01)
    assert not track.on_gps(fix_at(track, 80, 0, 2.0, 3.0))
    assert track.on_gps(fix_at(track, 80, 0, 3.0, 3.0))  # the third in a row: it moved
    assert track.x == pytest.approx(80.0, abs=0.01)


def test_a_track_put_right_by_gps_adds_no_distance_but_the_walk_on_does():
    # steps indoors drift 60 m from where the person is; outside, GPS puts the track right
    nav = Navigator()
    nav.on_gps(Fix(0.0, 31.5204, 74.3587, 4.0))
    walk_legs(nav, [(90, 20)], t0=1.0)  # 20 steps east, 14 m
    walked, x = nav.track.distance_m, nav.track.x
    t = 20.0
    for i in range(3):
        nav.on_gps(fix_at(nav.track, x, 60.0, t + i, 3.0))
    assert nav.track.y == pytest.approx(60.0, abs=0.5)  # the track is where GPS says
    assert nav.track.distance_m == pytest.approx(walked, abs=0.75)  # the jump was not walked
    walk_legs(nav, [(90, 10)], t0=t + 4.0)
    assert nav.track.distance_m == pytest.approx(walked + 7.0, abs=1.0)


@pytest.mark.parametrize("accuracy", [5.0, 10.0])
def test_a_device_rolling_with_no_steps_counts_its_travel(accuracy):
    # Waqas's case on #150: a chair rolls north at 1.1 m/s for 2 minutes, one fix a second; its
    # fixes leave the gate and the track follows by the agreeing-fixes jump. That is travel.
    track = Track()
    for i in range(121):
        track.on_gps(fix_at(track, 0.0, 1.1 * i, float(i), accuracy))
    assert track.y > 110.0
    assert track.distance_m == pytest.approx(track.y, abs=1.0)


def test_far_fixes_scattered_in_different_directions_do_not_move_the_track():
    # multipath while standing: three far fixes that do not agree with each other (Waqas, #137)
    track = Track()
    track.on_gps(fix_at(track, 0, 0, 0.0, 5.0))
    for i, (e, n) in enumerate([(80, 0), (0, -90), (-70, 40), (80, 0), (0, -90)]):
        assert not track.on_gps(fix_at(track, e, n, 1.0 + i, 10.0))
    assert math.hypot(track.x, track.y) < 0.01 and track.distance_m == 0.0


def test_after_scattered_fixes_three_that_agree_still_move_it():
    track = Track()
    track.on_gps(fix_at(track, 0, 0, 0.0, 5.0))
    track.on_gps(fix_at(track, 0, -90, 1.0, 10.0))  # one outlier first
    for i, e in enumerate([60, 62, 59]):  # then the person really is 60 m east
        moved = track.on_gps(fix_at(track, e, 1.0, 2.0 + i, 5.0))
    assert moved and track.x == pytest.approx(60.3, abs=0.1)


def test_a_gap_in_the_middle_of_a_swing_starts_over():
    counter = StepCounter()
    count(walk(5.0), counter)
    rising = [(5.0 + i / 30, 0.0, 0.0, G + 2.5) for i in range(4)]  # part of a rise, then a gap
    count(rising, counter)
    assert counter.above
    count([(9.0, 0.0, 0.0, G)], counter)  # 3.9 s later
    assert not counter.above and counter.pending is None


def test_a_still_person_on_gps_does_not_add_distance_from_the_gps_wander():
    track = Track()
    rnd = random.Random(4)
    for i in range(120):  # two minutes standing, the fix wandering by a few metres
        track.on_gps(fix_at(track, rnd.gauss(0, 2.5), rnd.gauss(0, 2.5), float(i), 5.0))
    assert track.distance_m < 10.0
    assert math.hypot(track.x, track.y) < 2.0


def test_the_speed_is_the_distance_over_the_last_seconds():
    nav = Navigator()
    t = walk_legs(nav, [(0, 20)], every=0.5)  # 0.7 m every 0.5 s: 1.4 m/s
    assert nav.track.speed(t) == pytest.approx(1.4, abs=0.15)
    assert nav.track.speed(t + 30.0) == 0.0  # standing since


def test_the_whole_chain_from_acceleration_to_position():
    nav = Navigator()
    nav.on_orientation(90.0, 0.0, 0.0)  # flat, top edge west
    for t, ax, ay, az in walk(10.0):
        nav.on_accel(t, ax, ay, az, now=t)
    v = nav.view(10.0)
    assert abs(v["steps"] - 18) <= 1
    assert v["x"] < -5.0 and abs(v["y"]) < 0.5  # walked west
    assert v["heading"] == 270


def test_shaking_faster_than_anyone_steps_is_not_counted():
    shaking = []
    for i in range(100):  # 10 s of a square wave at 5 Hz, as on a rough floor
        t = i / 10
        shaking.append((t, 0.0, 0.0, G + (6.0 if i % 2 else -6.0)))
    fine = [(i / 30, 0.0, 0.0, G + (3.0 if (i // 3) % 2 else -3.0)) for i in range(300)]
    assert count(shaking) == []
    assert count(fine) == []  # the same at 30 readings a second


# ---- marked places and the way back (#130)


def test_the_way_back_to_a_mark_as_a_clock_face():
    from demo.navigation import way_text

    nav = Navigator()
    nav.track.mark()
    walk_legs(nav, [(0, 20)])  # 14 m north, facing north
    assert way_text(nav.track.way_to()) == "The mark is 14 metres away, at 6 o'clock."
    nav.on_orientation(None, None, None, compass=90)  # turn to face east
    assert way_text(nav.track.way_to()) == "The mark is 14 metres away, at 3 o'clock."
    walk_legs(nav, [(180, 19)])  # back south, near the mark
    assert way_text(nav.track.way_to()) == "You are at the mark."


def test_the_way_back_without_a_compass_or_a_mark():
    from demo.navigation import way_text

    track = Track()
    assert way_text(track.way_to()) == "No mark yet. Say mark this place first."
    track.mark()
    track.x, track.y = 10.0, 10.0  # moved north-east, no compass
    assert way_text(track.way_to()) == "The mark is 14 metres to the south-west."


def test_a_walk_with_gps_is_saved_as_gpx_and_geojson():
    import json
    import xml.etree.ElementTree as ET

    from demo.navigation import to_geojson, to_gpx

    nav = Navigator()
    nav.on_gps(Fix(0.0, 31.5204, 74.3587, 4.0))
    nav.track.mark("door")
    walk_legs(nav, [(0, 10)])
    gpx = to_gpx(nav.track, wall_of=lambda t: 1_700_000_000.0 + t)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    root = ET.fromstring(gpx.encode())
    pts = root.findall(".//g:trkpt", ns)
    assert len(pts) >= 10 and root.find(".//g:wpt/g:name", ns).text == "door"
    assert float(pts[-1].get("lat")) > 31.5204 + 6.5 / 111_000  # 7 m north
    assert pts[-1].find("g:time", ns).text.startswith("2023-11-14T")
    geo = json.loads(json.dumps(to_geojson(nav.track)))
    line, door = geo["features"]
    assert line["geometry"]["type"] == "LineString" and "crs_note" not in geo
    assert door["geometry"]["coordinates"] == [74.3587, 31.5204]  # longitude first


def test_a_walk_indoors_with_no_fix_has_no_gpx_and_says_its_geojson_is_in_metres():
    from demo.navigation import to_geojson, to_gpx

    nav = Navigator()
    walk_legs(nav, [(90, 10)])
    assert to_gpx(nav.track) is None
    geo = to_geojson(nav.track)
    assert geo["features"][0]["geometry"]["coordinates"][-1] == [7.0, 0.0]
    assert "metres east and north" in geo["crs_note"]
