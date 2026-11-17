"""Which floor, from the phone's motion and the camera's stairs (demo.floors)."""

import random

from demo.floors import FloorTracker, label


def ride(tr, t0, profile, hz=30, noise=0.03, seed=1):
    """Feed a lift ride: profile is (seconds, vertical acceleration) pieces, the phone held flat."""
    rnd, t = random.Random(seed), t0
    for secs, a in profile:
        for _ in range(int(secs * hz)):
            t += 1 / hz
            tr.on_motion(
                t, rnd.gauss(0, noise), rnd.gauss(0, noise), 9.81 + a + rnd.gauss(0, noise)
            )
    return t


STILL = [(3.0, 0.0)]
UP_ONE = [(1.5, 0.8), (1.5, 0.0), (1.5, -0.8)]  # 0.9 + 1.8 + 0.9 = 3.6 m up


def test_a_lift_ride_up_one_floor_is_one_floor_up():
    tr = FloorTracker(floor=0)
    t = ride(tr, 0.0, STILL + UP_ONE + STILL)
    assert tr.floor == 1 and tr.events[-1]["source"] == "lift"
    assert "lift up about 3." in tr.events[-1]["text"]
    assert tr.view(t)["label"] == "1"


def test_a_lift_ride_down_two_floors():
    tr = FloorTracker(floor=3)
    t = ride(tr, 0.0, STILL + [(1.5, -0.8), (4.5, 0.0), (1.5, 0.8)] + STILL)  # 0.9 + 5.4 + 0.9 m
    assert tr.floor == 1 and tr.view(t)["mode"] == "still"


def test_a_bump_or_walking_is_not_a_lift():
    tr = FloorTracker(floor=0)
    ride(tr, 0.0, STILL + [(0.1, 3.0)] + STILL)  # a knock on the phone
    assert tr.floor == 0 and not tr.events
    t, steps = 10.0, 0
    for i in range(300):  # 10 s of walking: big swings, but steps are being counted
        t += 1 / 30
        if i % 17 == 0:
            steps += 1
            tr.on_steps(t, steps)
        tr.on_motion(t, 0.0, 0.0, 9.81 + (2.0 if (i // 8) % 2 else -2.0))
    assert tr.floor == 0 and tr.view(t)["mode"] == "walking"


def climb(tr, t, steps, n, direction=+1, seen=True, every=0.6):
    """n steps, one every `every` seconds, the camera seeing the stairs ahead on each or not."""
    for _ in range(n):
        t += every
        steps += 1
        if seen:
            tr.on_stairs(t, direction, 0.8)
        tr.on_steps(t, steps)
        tr.tick(t)
    return t, steps


def test_a_flight_of_stairs_seen_by_the_camera_then_climbed_is_a_floor():
    tr = FloorTracker(floor=0)
    tr.on_stairs(1.0, +1, 1.2)
    t, _ = climb(tr, 1.0, 0, 19)  # 19 risers of 17 cm: 3.2 m
    assert tr.view(t)["mode"] == "stairs up" and tr.view(t)["flight_steps"] == 19
    tr.tick(t + 6.0)
    assert tr.floor == 1 and tr.events[-1]["source"] == "stairs"
    assert tr.events[-1]["text"] == "Floor 1: 19 steps up the stairs"


def test_walking_past_stairs_is_not_climbing_them():
    """Waqas (#193): once stairs were seen, every step counted, so a walk down a corridor past a
    staircase went up floors."""
    tr = FloorTracker(floor=0)
    tr.on_stairs(0.0, +1, 3.5)  # seen down the corridor: ahead, not under foot
    t, steps = climb(tr, 0.0, 0, 60, seen=False, every=0.5)
    assert tr.floor == 0 and not tr.events
    tr.on_stairs(t, +1, 1.0)  # passing right by them, then on along the corridor
    t, steps = climb(tr, t, steps, 60, seen=False, every=0.5)
    assert tr.floor == 0 and tr.events[-1]["text"] == "2 steps on stairs: less than a floor"
    # half a flight, then along the landing without a pause: the risers and the first second
    t, steps = climb(tr, t + 10, steps, 10)
    t, steps = climb(tr, t, steps, 15, seen=False, every=0.5)
    assert tr.floor == 0 and tr.events[-1]["text"] == "12 steps on stairs: less than a floor"


def test_a_landing_turn_is_one_flight():
    tr = FloorTracker(floor=0)
    t, steps = climb(tr, 0.0, 0, 10)  # first half
    t, steps = climb(tr, t, steps, 4, seen=False)  # the landing: one step in, then out of sight
    t, steps = climb(tr, t, steps, 10)  # second half
    tr.tick(t + 6.0)
    assert tr.floor == 1 and tr.events[-1]["text"] == "Floor 1: 21 steps up the stairs"


def test_a_few_steps_on_stairs_are_not_a_floor_and_hand_corrections_win():
    tr = FloorTracker(floor=0)
    tr.on_stairs(0.0, -1)  # no distance (no tilt): taken as on them
    climb(tr, 0.0, 0, 3, direction=-1)
    tr.tick(10.0)
    assert tr.floor == 0 and "less than a floor" in tr.events[-1]["text"]
    tr.set_floor(11.0, -1)
    assert tr.view(11.0)["label"] == "B1" and tr.events[-1]["source"] == "hand"


def test_gps_altitude_is_kept_as_a_reference_only():
    tr = FloorTracker(floor=0)
    tr.on_gps_altitude(0.0, 540.0, 12.0)
    tr.on_gps_altitude(5.0, 547.5, 15.0)
    v = tr.view(6.0)
    assert v["gps_altitude"]["above_start_m"] == 7.5 and tr.floor == 0  # never moves the floor
    assert [label(n) for n in (-2, -1, 0, 1, 4)] == ["B2", "B1", "G", "1", "4"]


def test_started_indoors_the_floor_is_unknown_but_the_floors_moved_are_counted():
    tr = FloorTracker()  # the camera starts on some floor, nothing known before
    assert tr.view(0.0)["label"] == "?" and not tr.view(0.0)["known"]
    t = ride(tr, 0.0, STILL + UP_ONE + STILL)
    v = tr.view(t)
    assert (v["label"], v["moved"], v["height_m"]) == ("?", 1, 3.2)
    assert tr.events[-1]["text"].startswith("+1 floor: lift up")
    tr.set_floor(t + 1, 2)  # the supervisor says where this is
    assert tr.view(t + 1)["label"] == "2"


def test_walking_in_from_outside_starts_at_the_ground_floor():
    tr = FloorTracker()
    for t in range(10):
        tr.on_position_source(float(t), "gps")
    assert tr.view(9.0)["outdoors"] and tr.view(9.0)["label"] == "?"
    tr.on_position_source(12.0, "steps")  # GPS lost at the door
    v = tr.view(12.0)
    assert v["label"] == "G" and not v["outdoors"] and tr.events[-1]["source"] == "gps"
    tr2 = FloorTracker()
    tr2.on_position_source(0.0, "steps")  # never outside: still unknown
    assert tr2.view(0.0)["label"] == "?"


def test_the_imagery_date_comes_from_the_newest_dated_layer():
    from demo.tiles import imagery_info

    reply = {
        "results": [
            {"attributes": {"SRC_DATE2": "Null"}},  # the 15 m layer: no date
            {
                "attributes": {
                    "DATE (YYYYMMDD)": "20250828",
                    "RESOLUTION (M)": "0.46",
                    "SOURCE": "Vantor",
                    "SOURCE_INFO": "Vivid Advanced",
                    "ReleaseName": "Raster Basemaps 2026.R01",
                }
            },
            {
                "attributes": {
                    "DATE (YYYYMMDD)": "20190302",
                    "RESOLUTION (M)": "0.6",
                    "SOURCE": "Maxar",
                }
            },
        ]
    }
    info = imagery_info(reply)
    assert info["date"] == "28 Aug 2025" and info["source"] == "Vantor Vivid Advanced"
    assert info["resolution_m"] == 0.46 and imagery_info({"results": []}) is None


def test_the_indoor_page_and_the_floor_controls():
    import asyncio

    from aiohttp.test_utils import TestClient, TestServer

    from demo.server import build_app

    async def go():
        app = build_app()
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            page = await (await client.get("/indoor")).text()
            assert "/static/indoor.js" in page and "Floor" in page
            r = await client.get("/api/imagery?lat=nope&lon=1")
            assert r.status == 400
            ws = await client.ws_connect("/ws/dashboard")
            await ws.send_json({"type": "floor", "set": True})  # ignored: not floor 1
            await ws.send_json({"type": "floor", "height": 3.5})
            for _ in range(60):
                m = await asyncio.wait_for(ws.receive_json(), 3)
                if m.get("type") == "snapshot" and m["floors"]["floor_h"] == 3.5:
                    break
            assert m["floors"]["label"] == "?"
            await ws.send_json({"type": "floor", "set": 2})
            await ws.send_json({"type": "floor", "set": "the moon"})  # ignored
            for _ in range(60):
                m = await asyncio.wait_for(ws.receive_json(), 3)
                if m.get("type") == "snapshot" and m["floors"]["label"] == "2":
                    break
            assert m["floors"]["floor_h"] == 3.5 and "phone_online" in m
            await ws.close()
        finally:
            await client.close()

    asyncio.run(go())


def test_a_lift_right_after_a_walk_counts_though_the_steps_reach_the_track_late():
    import math as m

    tr, rnd, t = FloorTracker(floor=0), random.Random(1), 0.0
    for _ in range(150):  # 5 s of walking
        t += 1 / 30
        tr.on_motion(t, 0, 0, 9.81 + 1.6 * m.sin(2 * m.pi * 1.8 * t) + rnd.gauss(0, 0.03))
    t = ride(tr, t, [(4.0, 0.0)])  # standing in the lift
    tr.on_steps(t, 9)  # the walk's steps arrive now, as a batch
    ride(tr, t, UP_ONE + STILL + STILL)
    assert tr.floor == 1 and tr.events[-1]["source"] == "lift"


def test_the_floor_said_by_the_user():
    from demo.middleware import Middleware, floor_in, in_sentence

    assert floor_in("floor two") == floor_in("Second floor.") == floor_in("floor 2") == 2
    assert floor_in("ground floor") == 0 and floor_in("basement") == -1
    assert floor_in("the floor is wet") is None and floor_in("two") is None
    # the number next to "floor", not the first one in the phrase (Waqas, #196)
    assert floor_in("two people on the first floor") == floor_in("first floor, two people") == 1
    assert floor_in("I am on floor 3 with two friends") == 3 and floor_in("floor 99") is None
    assert in_sentence("where do I go") and not in_sentence("Stop.") and not in_sentence("go go")
    m = Middleware()
    m.on_heard("I am on the first floor", 5.0)
    assert m.floor_said == (1, 5.0) and m.inputs[-1]["command"] == "Floor 1"  # not "on"
    m.on_heard("Stop, there is a wall", 6.0)
    assert m.inputs[-1]["command"] == "Stop"


def test_an_imagery_service_error_is_not_kept_as_no_date(monkeypatch):
    """Waqas (#193): an error reply was kept as "no date here" for good."""
    import asyncio

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from demo import tiles
    from demo.server import build_app

    good = {"results": [{"attributes": {"DATE (YYYYMMDD)": "20250828"}}]}
    replies = [web.json_response({"error": {"code": 500}}), web.json_response(good)]

    async def upstream(request):
        return replies.pop(0)

    async def go():
        fake = web.Application()
        fake.router.add_get("/identify", upstream)
        service = TestServer(fake)
        await service.start_server()
        monkeypatch.setattr(tiles, "IMAGERY_INFO", str(service.make_url("/identify")) + "?x={lat}")
        client = TestClient(TestServer(build_app()))
        await client.start_server()
        try:
            r = await client.get("/api/imagery?lat=33.65&lon=73.02")
            assert r.status == 503
            r = await client.get("/api/imagery?lat=33.65&lon=73.02")  # the service is back
            assert r.status == 200 and (await r.json())["date"] == "28 Aug 2025"
        finally:
            await client.close()
            await service.close()

    asyncio.run(go())


def test_a_phone_tilted_during_the_ride_still_reads_the_floors():
    """Waqas (#193): gravity is kept from before the ride, so a tilt during it reads as a steady
    push. The speed left at the end is taken out as drift, and the end is judged on a steady
    reading (the old code saw neither ride end at 7 degrees)."""
    import math as m

    for profile, floors in ((UP_ONE, 1), ([(1.5, 0.8), (4.5, 0.0), (1.5, -0.8)], 2)):
        acc = [a for secs, a in STILL + profile + [(8.0, 0.0)] for _ in range(int(secs * 30))]
        ride_n = len(acc) - 11 * 30
        tr, rnd, t = FloorTracker(floor=0), random.Random(1), 0.0
        for i, a in enumerate(acc):
            t += 1 / 30
            # tilts to 7 degrees over the first fifth of the ride, and stays tilted
            th = m.radians(7) * min(max((i - 90) / (0.2 * ride_n), 0.0), 1.0)
            g = 9.81 + a
            z = g * m.cos(th) + rnd.gauss(0, 0.03)
            tr.on_motion(t, g * m.sin(th) + rnd.gauss(0, 0.03), rnd.gauss(0, 0.03), z)
        assert tr.floor == floors, tr.events
