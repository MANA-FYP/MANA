"""demo.field (#133): a made-up recording of a square walk, scored as a field test would be."""

import importlib.util
import json
import math

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiohttp") is None, reason="needs aiohttp")
G = 9.81


def square_walk(folder, steps_a_side=20, cadence=1.8, rate=30):
    """A recording of a walk round a square on steps alone: the compass turns 90 degrees at each
    corner, the phone's own clock in each motion reading."""
    folder.mkdir(parents=True)
    (folder / "frames").mkdir()
    lines, t = [], 0.0
    side_s = steps_a_side / cadence
    for side in range(4):
        lines.append({"t": round(t, 3), "msg": {"type": "orientation", "alpha": None, "beta": 70,
                                                 "gamma": 0, "compass": side * 90}})  # fmt: skip
        for i in range(int(side_s * rate)):
            phone_t = t + i / rate
            az = G + 1.5 * math.sin(2 * math.pi * cadence * phone_t)
            msg = {"type": "motion", "ax": 0, "ay": 0, "az": az, "t": round(phone_t, 4)}
            lines.append({"t": round(phone_t, 4), "msg": msg})
        t += side_s
    with (folder / "session.jsonl").open("w", encoding="utf-8") as f:
        f.writelines(json.dumps(line) + "\n" for line in lines)
    return 4 * steps_a_side


def test_a_square_walk_on_steps_comes_back_near_its_start(tmp_path, capsys):
    from demo import field

    taken = square_walk(tmp_path / "loop")
    assert field.main([str(tmp_path / "loop"), "--loop", "--steps", str(taken),
                       "--place", "made up", "--speed", "8"]) == 0  # fmt: skip
    out = capsys.readouterr().out
    result = dict(line.split(": ", 1) for line in out.splitlines() if ": " in line)
    assert abs(int(result["steps"]) - taken) <= 3
    assert float(result["gap_m"]) < 0.1 * float(result["distance_m"])  # back near the start
    assert "| made up | steps |" in out and f" / {taken} |" in out


def test_the_score_of_a_known_route():
    from demo.field import score
    from demo.navigation import Track

    track = Track()
    track.steps, track.distance_m, track.x, track.y = 95, 66.0, 1.5, 2.0
    s = score(track, loop=True, distance_m=60.0, steps=100)
    assert s["steps_error"] == "-5.0%" and s["distance_error"] == "+10.0%"
    assert s["gap_m"] == 2.5 and s["gap_share"] == "3.8% of the walk"


def test_no_recording_says_so(tmp_path, capsys):
    from demo.field import main

    assert main([str(tmp_path)]) == 2 and "no recording" in capsys.readouterr().err


def test_the_date_is_the_day_the_walk_began_not_the_day_it_was_copied(tmp_path, capsys):
    # a recording copied to another laptop gets new file dates (Waqas, #164)
    import os

    from demo import field
    from demo.recording import Recorder, started

    rec = Recorder(tmp_path / "walk")
    rec.close()
    kept = json.loads((tmp_path / "walk" / "meta.json").read_text())["started"]
    assert started(tmp_path / "walk") == kept[:10]
    (tmp_path / "walk" / "meta.json").write_text(
        json.dumps({"started": "2026-10-01T09:30:00+00:00"})
    )
    os.utime(tmp_path / "walk" / "session.jsonl", (1_900_000_000, 1_900_000_000))  # copied in 2030
    field.main([str(tmp_path / "walk"), "--speed", "50"])
    assert "| 2026-10-01 |" in capsys.readouterr().out
    assert started(tmp_path) is None  # no meta.json: the file's date, as before
