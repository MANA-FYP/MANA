"""Tests for scoring the phone walks against hand marks (#89)."""

import json

import pytest

from tools import score_walks as sw
from tools.score_walks import Decision, Mark


def test_a_reaction_in_the_window_for_a_fitting_reason_catches_the_hazard():
    marks = [Mark("w", "stairs_up", 10.0, 15.0)]
    decided = [
        Decision(9.0, "slow", "Stairs ahead, changing to tracks"),  # before it is in view,
        Decision(9.5, "go", "Level ground"),  # and over before start_s: not a reaction to it
        Decision(12.0, "slow", "Ramp ahead"),  # the wrong hazard
        Decision(12.5, "slow", "Stairs ahead, changing to tracks"),
    ]
    (row,), _ = sw.score(marks, decided)
    assert row["caught"] and row["reaction_s"] == 2.5 and row["lead_s"] == 2.5
    assert row["decision"] == "slow: Stairs ahead, changing to tracks"


def test_too_late_or_no_reaction_is_a_miss():
    marks = [Mark("w", "drop", 10.0, 12.0), Mark("w", "person", 20.0, 25.0)]
    decided = [Decision(12.5, "stop", "Drop ahead, stopping")]  # after the edge
    rows, false = sw.score(marks, decided)
    assert [r["caught"] for r in rows] == [False, False]
    assert [d.walk_s for d in false] == [12.5]  # outside every window, so a false alarm too


def test_false_alarms_leave_out_mana_s_own_state_and_count_only_unexplained_ones():
    marks = [Mark("w", "curb", 30.0, 33.0)]
    decided = [
        Decision(1.0, "slow", "Ground sensors not available, slowing down"),  # its own state
        Decision(5.0, "slow", "Wheel slip, changing to tracks"),
        Decision(8.0, "slow", "On tracks until the ground is level"),
        Decision(12.0, "stop", "Obstacle ahead, stopping"),  # nothing marked there
        Decision(31.0, "slow", "Possible step ahead, slowing down"),  # the curb: caught
        Decision(40.0, "slow", "Ramp ahead"),  # nothing marked there
    ]
    rows, false = sw.score(marks, decided)
    assert rows[0]["caught"]
    assert [d.why for d in false] == ["Obstacle ahead, stopping", "Ramp ahead"]


def test_stairs_down_is_not_stairs_up():
    assert sw.fits("Stairs down ahead. Say yes to go down on tracks", "stairs_down")
    assert not sw.fits("Stairs down ahead. Say yes to go down on tracks", "stairs_up")
    assert sw.fits("Someone may be near", "person") and not sw.fits("Someone may be near", "door")


def test_marks_are_checked_when_read(tmp_path):
    good = tmp_path / "marks.csv"
    good.write_text("recording,hazard,start_s,end_s,note\nw,ramp,1,4,the library ramp\n")
    assert sw.read_marks(good) == [Mark("w", "ramp", 1.0, 4.0, "the library ramp")]
    bad = tmp_path / "bad.csv"
    bad.write_text("recording,hazard,start_s,end_s\nw,lift,1,4\n")
    with pytest.raises(ValueError, match="lift"):
        sw.read_marks(bad)
    bad.write_text("recording,hazard,start_s,end_s\nw,ramp,5,4\n")
    with pytest.raises(ValueError, match="before"):
        sw.read_marks(bad)


def test_main_scores_each_recording_and_prints_the_table(tmp_path, capsys):
    (tmp_path / "marks.csv").write_text(
        "recording,hazard,start_s,end_s\na,stairs_up,10,15\na,person,30,34\nb,drop,5,8\n"
    )
    out = tmp_path / "decided"
    out.mkdir()
    (out / "a.csv").write_text(
        "walk_s,n,kind,state,why,text\n"
        '11,1,decision,slow,"Stairs ahead, changing to tracks",x\n'
        '20,2,decision,stop,"Obstacle ahead, stopping",x\n'
        "21,3,link,,,Wi-Fi is available\n"
    )
    (out / "b.csv").write_text(
        'walk_s,n,kind,state,why,text\n6,1,decision,stop,"Drop ahead, stopping",x\n'
    )
    walk = tmp_path / "rec" / "a"
    walk.mkdir(parents=True)
    (walk / "session.jsonl").write_text("".join(json.dumps({"t": t}) + "\n" for t in (0, 60, 120)))
    rows_csv = tmp_path / "rows.csv"
    args = ["--marks", str(tmp_path / "marks.csv"), "--decisions", str(out)]
    args += ["--recordings", str(tmp_path / "rec"), "--out", str(rows_csv)]
    assert sw.main(args) == 0
    text = capsys.readouterr().out
    assert "false alarm in a at 20.0 s: stop: Obstacle ahead, stopping" in text
    assert "| stairs_up | 1 | 1 | 1.0 s | 4.0 s |" in text
    assert "| person | 1 | 0 | - | - |" in text
    assert "| all | 3 | 2 | | |" in text
    # a's length from its session (2 min), b's from its last decision (0.1 min)
    assert "False alarms: 1 in 2.1 minutes of walking (0.48 a minute)." in text
    assert rows_csv.read_text().count("\n") == 4


def test_a_recording_with_no_decisions_is_named(tmp_path, capsys):
    (tmp_path / "marks.csv").write_text("recording,hazard,start_s,end_s\nz,ramp,1,2\n")
    assert sw.main(["--marks", str(tmp_path / "marks.csv"), "--decisions", str(tmp_path)]) == 2
    assert "no decisions for z" in capsys.readouterr().err


def test_a_reaction_held_from_just_before_the_mark_is_caught_not_a_false_alarm():
    """Mushahid's case on #221: MANA slows at 9.6 s for a step marked from 10.0 s and holds it
    past the hazard; the demo writes one row, at its start."""
    marks = [Mark("w", "stairs_up", 10.0, 15.0)]
    decided = [
        Decision(9.6, "slow", "Possible step ahead, slowing down"),
        Decision(16.0, "go", "Level ground"),
    ]
    (row,), false = sw.score(marks, decided)
    assert row["caught"] and row["reaction_s"] == 0.0 and row["lead_s"] == 5.0
    assert false == []


def test_a_reaction_held_from_long_before_does_not_answer_a_later_mark():
    marks = [Mark("w", "person", 30.0, 34.0)]
    decided = [Decision(20.0, "stop", "Obstacle ahead, stopping")]  # held to the end
    (row,), false = sw.score(marks, decided)
    assert not row["caught"] and [d.walk_s for d in false] == [20.0]
    (row,), false = sw.score(marks, decided, early_s=10.0)  # unless --early allows it
    assert row["caught"] and false == []


def test_a_walk_with_nothing_marked_is_scored_too(tmp_path, capsys):
    (tmp_path / "marks.csv").write_text("recording,hazard,start_s,end_s\na,ramp,5,9\n")
    out = tmp_path / "decided"
    out.mkdir()
    head = "walk_s,n,kind,state,why,text\n"
    (out / "a.csv").write_text(head + '6,1,decision,slow,"Ramp ahead",x\n')
    (out / "flat.csv").write_text(
        head
        + '30,1,decision,stop,"Obstacle ahead, stopping",x\n'
        + '31,2,decision,go,"Level ground",x\n'
        + "60,3,decision,go,,x\n"
    )
    assert sw.main(["--marks", str(tmp_path / "marks.csv"), "--decisions", str(out)]) == 0
    text = capsys.readouterr().out
    assert "2 walks scored, 1 of them with no hazard marked" in text
    assert "false alarm in flat at 30.0 s: stop: Obstacle ahead, stopping" in text
    assert "False alarms: 1 in 1.1 minutes" in text  # 0.1 min for a, 1.0 for flat


def test_a_walk_with_no_decision_is_scored(tmp_path, capsys):
    (tmp_path / "marks.csv").write_text("recording,hazard,start_s,end_s\na,ramp,5,9\n")
    out = tmp_path / "decided"
    out.mkdir()
    head = "walk_s,n,kind,state,why,text\n"
    (out / "a.csv").write_text(head + '6,1,decision,slow,"Ramp ahead",x\n')
    (out / "quiet.csv").write_text(head + "4,1,way,,,Way open\n")
    assert sw.main(["--marks", str(tmp_path / "marks.csv"), "--decisions", str(out)]) == 0
    assert "2 walks scored, 1 of them with no hazard marked" in capsys.readouterr().out
    assert sw.held([]) == []
