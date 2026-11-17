"""The scoring of tools/sus_score.py (#90): Brooke's rule, and what is refused."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import sus_score as sus  # noqa: E402

BEST = [5, 1, 5, 1, 5, 1, 5, 1, 5, 1]
WORST = [1, 5, 1, 5, 1, 5, 1, 5, 1, 5]


def test_the_best_and_the_worst_answers_score_100_and_0():
    assert sus.score(BEST) == 100.0
    assert sus.score(WORST) == 0.0


def test_all_neutral_answers_score_50():
    assert sus.score([3] * 10) == 50.0


def test_odd_items_count_the_answer_minus_one_and_even_items_five_minus_it():
    # only q1 is agreed: 4 points of 40, and the other items are the worst for their kind
    assert sus.score([5, 5, 1, 5, 1, 5, 1, 5, 1, 5]) == 10.0
    # only q2 is disagreed: 4 points
    assert sus.score([1, 1, 1, 5, 1, 5, 1, 5, 1, 5]) == 10.0


@pytest.mark.parametrize("bad", [[3] * 9, [3] * 11, [0] + [3] * 9, [6] + [3] * 9, [3.5] + [3] * 9])
def test_the_wrong_number_or_range_of_answers_is_refused(bad):
    with pytest.raises(ValueError):
        sus.score(bad)


def write(path, rows):
    head = "participant," + ",".join(f"q{i}" for i in range(1, 11))
    path.write_text("\n".join([head, *rows]) + "\n")


def test_a_file_is_read_and_summarised(tmp_path):
    f = tmp_path / "a.csv"
    write(f, ["P01," + ",".join(map(str, BEST)), "P02," + ",".join(map(str, WORST))])
    answers = sus.read(str(f))
    assert list(answers) == ["P01", "P02"]
    text = sus.summary(answers)
    assert "| P01 | 100.0 |" in text and "| P02 | 0.0 |" in text
    assert "2 participants: mean 50.0" in text and "rough guide" in text


def test_a_blank_answer_names_the_person_and_the_item(tmp_path):
    f = tmp_path / "b.csv"
    write(f, ["P03,5,1,5,1,,1,5,1,5,1"])
    with pytest.raises(ValueError, match=r"P03: q5 is ''"):
        sus.read(str(f))


def test_a_repeated_participant_is_refused(tmp_path):
    f = tmp_path / "c.csv"
    write(f, ["P01," + ",".join(map(str, BEST)), "P01," + ",".join(map(str, BEST))])
    with pytest.raises(ValueError, match="P01 appears twice"):
        sus.read(str(f))


def test_the_command_says_what_is_wrong_and_does_not_crash(tmp_path, capsys):
    assert sus.main([]) == 2
    assert sus.main([str(tmp_path / "missing.csv")]) == 1
    assert "cannot score" in capsys.readouterr().out
