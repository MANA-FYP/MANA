"""The threshold sweep of tools/sweep_thresholds.py (#86): one run, one value, and its table."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from mana_core.decision import Limits
from mana_core.scenarios import SCENARIOS
from sweep_charts import tables  # noqa: E402
from sweep_thresholds import VALUES, main, markdown, one, sweep  # noqa: E402


class InProcess:
    """Stands in for the process pool, so the test runs in this process."""

    def map(self, fn, jobs, chunksize=1):
        return map(fn, jobs)


def test_a_clean_noisy_level_run_meets_its_marks_at_full_speed():
    # seed 2 meets every mark at full speed; seed 1 slows for 0.1 s, from its noise
    met, mishap, slowed, reached = one(("level", Limits(), 2))
    assert met and not mishap
    assert slowed == 0 and reached > 0.0
    assert one(("level", Limits(), 1))[2] > 0  # the sweep counts that


def test_every_swept_list_has_the_default_value():
    for param, values in VALUES.items():
        assert getattr(Limits(), param) in values, param


def test_the_table_has_a_row_per_value_and_marks_the_default():
    rows = sweep("step_m", 1, 1, InProcess())
    assert [r["value"] for r in rows] == VALUES["step_m"]
    assert all(r["runs"] == len(SCENARIOS) for r in rows)  # one run of each scenario
    table = markdown("step_m", rows)
    assert "| **2.0** (now) |" in table and len(table.splitlines()) == 2 + len(rows)


def test_an_unknown_threshold_is_refused(capsys):
    assert main(["--runs", "1", "edge"]) == 2
    assert "unknown: edge" in capsys.readouterr().out


def test_the_chart_reads_d8s_tables_with_the_value_kept():
    d8 = (Path(__file__).resolve().parents[1] / "docs" / "decisions.md").read_text(encoding="utf-8")
    data = tables(d8)
    assert set(data) == set(VALUES)
    for param, rows in data.items():
        assert [v for v, *_ in rows] == VALUES[param]
        assert [v for v, kept, *_ in rows if kept] == [getattr(Limits(), param)]
    assert dict((v, m) for v, _, _, m in data["edge_m"])[0.2] == 4  # mishaps, from the table
