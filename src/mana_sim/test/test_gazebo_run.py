"""A run in Gazebo that could not be played. No ROS, but for the last test (rclpy)."""

import pytest
from mana_core.scenarios import Trace

from mana_sim import gazebo_run as gr
from mana_sim import gazebo_scenarios as gs


def test_the_runner_says_why_there_is_no_run_to_score():
    log = "[INFO] waiting\n[ERROR] [scenario_runner]: run not played: the chair did not settle\n"
    assert gr.why_not_played(log, 1) == "the chair did not settle"
    assert gr.why_not_played("", None) == "the runner did not finish in time"
    assert gr.why_not_played("", -9) == "the runner did not finish in time"
    assert gr.why_not_played("", 2) == "the runner exited with 2 and wrote no trace"


def test_a_run_not_played_fails_every_mark_with_the_reason():
    marks = gr.not_played("dark", "the core did not answer")
    assert len(marks) == len(gs.RUNS["dark"].marks) + 1  # and no mishap
    assert all(not ok and said == "not played: the core did not answer" for _, ok, said in marks)


def test_a_failed_run_is_scored_and_does_not_stop_the_others(monkeypatch, tmp_path):
    def play(name, traces):
        if name == "level":
            raise gr.RunFailed("the runner did not finish in time")
        path = traces / f"{name}.json"
        path.write_text(gs.trace_to_json(Trace()))
        return path

    monkeypatch.setattr(gr, "play", play)
    level = gr.results("level", tmp_path)
    assert level and all(not ok for _, ok, _ in level)
    assert "not played: the runner did not finish in time" in level[0][2]
    assert not (tmp_path / "level.json").exists()
    stop = gr.results("stop", tmp_path)
    assert [text for text, _, _ in stop][0] == gs.RUNS["stop"].marks[0].text


def test_a_trace_on_disk_is_scored_without_playing_unless_rerun(monkeypatch, tmp_path):
    (tmp_path / "stop.json").write_text(gs.trace_to_json(Trace()))
    played = []
    monkeypatch.setattr(
        gr, "play", lambda name, traces: played.append(name) or traces / "stop.json"
    )
    gr.results("stop", tmp_path, rerun=False)
    assert played == []
    gr.results("stop", tmp_path, rerun=True)
    assert played == ["stop"]


def test_the_runner_writes_a_trace_for_a_run_and_none_for_a_setup_failure(tmp_path):
    rclpy = pytest.importorskip("rclpy")
    from mana_sim.scenario_runner import ScenarioRunner

    rclpy.init()
    try:
        for why, failed in (("the chair did not settle", True), ("time limit", False)):
            out = tmp_path / f"{failed}.json"
            node = ScenarioRunner(gs.RUNS["level"], str(out), str(tmp_path / "ready"))
            with pytest.raises(SystemExit):
                node.finish(why, failed=failed)
            assert node.failed == failed and out.exists() == (not failed)
            node.destroy_node()
    finally:
        rclpy.shutdown()
