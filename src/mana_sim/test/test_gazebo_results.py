"""The results page of the scenarios in Gazebo. No ROS, no Gazebo."""

from mana_core.decision import Mode
from mana_core.scenarios import Sample, Trace

from mana_sim import gazebo_results as gr
from mana_sim import gazebo_scenarios as gs


def level_trace(reached: float) -> Trace:
    trace = Trace()
    for i in range(1, 60):
        t = i / 10
        trace.samples.append(Sample(t, t * 1.2, Mode.WHEELS, False, 1.0, "Level ground"))
    trace.end_m = reached
    return trace


def test_the_page_lists_the_marks_with_scripted_and_gazebo_results(tmp_path):
    (tmp_path / "level.json").write_text(gs.trace_to_json(level_trace(4.6)))
    page = gr.results_markdown(tmp_path)
    assert "| `level` | Full speed on wheels | PASS | PASS |" in page
    assert "1 of 4 pass marks" not in page and "4 of 4 pass marks are met" in page
    assert "| `stairs` | (not played)" in page  # the other nine have no trace here


def test_a_failing_mark_names_its_issue_and_one_without_an_issue_is_flagged(tmp_path):
    (tmp_path / "level.json").write_text(gs.trace_to_json(level_trace(1.0)))  # does not get far
    page = gr.results_markdown(tmp_path)
    assert "| `level` | Reaches the end of the route | PASS | FAIL |" in page
    assert "**none yet**" in page  # level has no known issue
    assert all(isinstance(n, int) for n in gr.KNOWN_ISSUES.values())


def test_the_routes_table_has_a_row_for_every_scenario():
    table = gr.routes_table()
    assert all(f"| `{name}` |" in table for name in gs.RUNS)


def test_the_section_replaces_its_old_self_in_the_file_and_keeps_the_rest(tmp_path):
    page = tmp_path / "results.md"
    page.write_text("# Results\n\nthe noisy runs\n")
    gr.update_file(page, gr.START + "\nfirst\n" + gr.END + "\n")
    gr.update_file(page, gr.START + "\nsecond\n" + gr.END + "\n")
    text = page.read_text()
    assert text.startswith("# Results\n\nthe noisy runs\n")
    assert "second" in text and "first" not in text and text.count(gr.START) == 1


def test_the_notes_on_missed_marks_take_their_numbers_from_the_traces():
    notes = gr.why_marks_fail(
        [
            (
                "Stops before the drop",
                "stopped, then let the stop go at 7.2 s, 0.67 m before the drop",
            ),
            (
                "Stops before the person",
                "stopped, then let the stop go at 3.4 s, 0.91 m before the person",
            ),
        ]
    )
    assert all(len(line) <= 100 for line in notes.splitlines())
    notes = " ".join(notes.split())  # as the page reads, without the line breaks
    assert "let go at 3.4 to 7.2 s" in notes and "0.67 to 0.91 m before" in notes
    assert "(2 marks)" in notes
    assert gr.why_marks_fail([]) == "- none"
