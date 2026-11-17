"""The summary of a drive, and where the baseline decided differently. No ROS: traces only."""

import pytest
from mana_core.decision import Command, Mode
from mana_core.scenarios import Message, Sample, Trace

from mana_sim import drive_summary as ds
from mana_sim import gazebo_scenarios as gs


def trace(*steps, end_m=None, said=(), mishaps=()):
    """steps: (x_m, mode, stop, speed_limit, reason), one every 0.1 s."""
    samples = [Sample(round(i * 0.1, 3), *step) for i, step in enumerate(steps)]
    return Trace(
        samples=samples,
        messages=[Message(0.0, "hello", False)],
        said=list(said),
        mishaps=list(mishaps),
        end_m=samples[-1].x_m if end_m is None else end_m,
    )


W, T = Mode.WHEELS, Mode.TRACKS
FREE = (W, False, 1.0, "Driving")


def test_a_drive_is_summarised_with_its_stops_slowdowns_and_time_on_tracks():
    t = trace(
        (0.0, *FREE),
        (0.5, W, False, 0.3, "Obstacle ahead, slowing down"),
        (0.8, W, False, 0.3, "Obstacle ahead, slowing down"),
        (1.0, W, True, 0.0, "Obstacle ahead, stopping"),
        (1.0, W, True, 0.0, "Obstacle ahead, stopping"),
        (1.1, T, False, 1.0, "Steps ahead, changing to tracks"),
        (1.5, T, False, 1.0, "On tracks"),
        (2.0, *FREE),
        (2.5, *FREE),
        said=[(0.2, Command.MANUAL), (0.9, Command.STOP), (1.2, Command.AUTOMATIC)],
    )
    s = ds.summarise("demo", t)
    assert s.duration_s == pytest.approx(0.8) and s.distance_m == 2.5 and s.messages == 1
    assert s.tracks_s == pytest.approx(
        0.2
    )  # from the sample on tracks to the next one that is on tracks
    assert [(e.reason, e.x_m) for e in s.stops] == [("Obstacle ahead, stopping", 1.0)]
    assert round(s.stops[0].length_s, 3) == 0.2
    assert [e.reason for e in s.slowdowns] == ["Obstacle ahead, slowing down"]
    assert s.takeovers == 2  # to manual and back; the stop is not a takeover
    assert s.mishaps == ()


def test_an_episode_that_lasts_to_the_end_of_the_drive_is_counted():
    s = ds.summarise(
        "x",
        trace(
            (0.0, *FREE),
            (0.2, W, True, 0.0, "Stopped by you"),
            (0.2, W, True, 0.0, "Stopped by you"),
        ),
    )
    assert [e.reason for e in s.stops] == ["Stopped by you"]


def test_the_first_difference_is_where_the_two_decide_differently_on_the_route():
    mana = trace(
        *[(x, *FREE) for x in (0, 1, 2, 3)],
        *[(x, W, True, 0.0, "Drop ahead, stopping") for x in (4, 5, 6)],
    )
    base = trace(*[(x, *FREE) for x in range(0, 8)])
    x, m, b = ds.first_difference(mana, base)
    assert x == 4 and m.stop and not b.stop
    assert ds.first_difference(mana, mana) is None


def test_a_one_sample_difference_is_a_timing_blip_and_not_a_difference():
    base = trace(*[(x, *FREE) for x in range(0, 6)])
    blip = trace((0, *FREE), (1, *FREE), (2, W, True, 0.0, "x"), (3, *FREE), (4, *FREE), (5, *FREE))
    assert ds.first_difference(blip, base) is None


def test_the_comparison_names_the_difference_and_both_mishaps():
    mana = trace(*[(x, *FREE) for x in range(0, 4)], end_m=3)
    base = trace(
        *[(x, W, False, 0.3, "slow") for x in range(0, 4)], end_m=3, mishaps=["over a drop"]
    )
    row = ds.comparison_row(
        "`d`", ds.summarise("d", mana), ds.summarise("d", base), ds.first_difference(mana, base)
    )
    assert (
        "| 0.0 m: MANA wheels at 100% (Driving); baseline wheels at 30% (slow)" in row
        or "0.0 m: MANA" in row
    )
    assert row.count("| none |") >= 1 and "| 1 |" in row


def test_the_section_is_written_from_the_traces_and_replaces_itself(tmp_path):
    mana = trace((0.0, *FREE), (1.0, *FREE), (2.0, *FREE), (3.0, *FREE))
    (tmp_path / "stop.json").write_text(gs.trace_to_json(mana))
    (tmp_path / "baseline").mkdir()
    (tmp_path / "baseline" / "stop.json").write_text(gs.trace_to_json(mana))
    section = ds.report_markdown(tmp_path)
    assert section.startswith(ds.START) and section.rstrip().endswith(ds.END)
    assert "`stop`" in section and "the same everywhere" in section and "1 drives." in section
    page = tmp_path / "results.md"
    page.write_text("# Results\n\ntext\n")
    ds.update_file(page, section)
    ds.update_file(page, section.replace("1 drives.", "again"))
    text = page.read_text()
    assert text.count(ds.START) == 1 and "again" in text and text.startswith("# Results")


def test_a_missing_folder_is_said(tmp_path, capsys):
    assert ds.main([str(tmp_path / "nothing")]) == 2
    assert "play the scenarios first" in capsys.readouterr().out


def test_the_start_up_wait_for_the_ground_sensors_is_not_a_difference():
    waiting = (W, False, 0.3, "Waiting for the ground sensors (1 of 2 up: the camera)")
    mana = trace(*[(x, *waiting) for x in range(0, 3)], *[(x, *FREE) for x in range(3, 8)])
    base = trace(*[(x, *FREE) for x in range(0, 8)])
    assert ds.first_difference(mana, base) is None
