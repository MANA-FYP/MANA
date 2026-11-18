"""The scoring of tools/command_words.py (#46): the noise mixing, the outcome of a clip and the
summary. Vosk and Speech Commands are not needed."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import command_words as cw  # noqa: E402


@pytest.mark.parametrize("snr", [20.0, 10.0, 0.0])
def test_noise_is_mixed_in_at_the_asked_signal_to_noise_ratio(snr):
    rng = np.random.default_rng(1)
    speech = (rng.standard_normal(16000) * 3000).astype(np.int16)
    noise = (rng.standard_normal(20000) * 500).astype(np.int16)
    mixed = cw.mix(speech, noise, snr).astype(np.float64)
    added = mixed - speech
    got = 10 * np.log10(np.mean(speech.astype(np.float64) ** 2) / np.mean(added**2))
    assert got == pytest.approx(snr, abs=0.1)


def test_silent_noise_leaves_the_speech_as_it_is():
    speech = np.arange(100, dtype=np.int16)
    assert (cw.mix(speech, np.zeros(100, dtype=np.int16), 5.0) == speech).all()


@pytest.mark.parametrize(
    ("label", "heard", "result"),
    [
        ("stop", [("stop", 0.9)], "right"),
        ("stop", [("go", 0.9)], "wrong"),
        ("stop", [], "missed"),
        ("bed", [], "quiet"),
        ("bed", [("go", 0.4)], "false"),
        ("go", [("go", 0.8), ("stop", 0.7)], "right"),  # the first word is the answer
    ],
)
def test_a_clip_is_right_wrong_missed_quiet_or_a_false_activation(label, heard, result):
    assert cw.outcome(label, heard) == result


def test_the_summary_counts_command_clips_and_other_words_apart():
    rows = [
        {"label": "stop", "heard": "stop", "outcome": "right"},
        {"label": "go", "heard": "", "outcome": "missed"},
        {"label": "bed", "heard": "go", "outcome": "false"},
        {"label": "cat", "heard": "", "outcome": "quiet"},
    ]
    s = cw.summary(rows)
    assert {k: s[k] for k in ("said", "right", "wrong", "missed", "other", "false")} == {
        "said": 2, "right": 1, "wrong": 0, "missed": 1, "other": 2, "false": 1,
    }  # fmt: skip


def test_stop_gets_its_own_counts_and_held_out_words_theirs():
    rows = [
        {"label": "stop", "heard": "", "outcome": "missed"},
        {"label": "stop", "heard": "go", "outcome": "wrong"},
        {"label": "stop", "heard": "up", "outcome": "wrong"},
        {"label": "stop", "heard": "stop", "outcome": "right"},
        {"label": "go", "heard": "stop", "outcome": "wrong"},
        {"label": "_silence_", "heard": "off", "outcome": "false"},
        {"label": "_silence_", "heard": "", "outcome": "quiet"},
        {"label": "visual", "heard": "on", "outcome": "false"},
        {"label": "bed", "heard": "", "outcome": "quiet"},
    ]
    s = cw.summary(rows, frozenset({"visual", "learn"}))
    assert (s["stop"], s["stop_missed"], s["stop_as_go"], s["stop_as_other"]) == (4, 1, 1, 1)
    assert s["go_as_stop"] == 1
    assert (s["silence"], s["silence_false"]) == (2, 1)
    assert (s["held"], s["held_false"]) == (1, 1)
    # noise alone is not another word: the official set's 408 other words are their own rate
    assert (s["other"], s["false"]) == (2, 1)


def test_the_test_noise_is_never_the_training_noise():
    noise = np.arange(1000)
    train, test = cw.noise_part(noise, "train"), cw.noise_part(noise, "test")
    assert train.size == 750 and test.size == 250
    assert not set(train.tolist()) & set(test.tolist())
    assert np.array_equal(np.concatenate([train, test]), noise)


def test_the_official_test_set_is_read_by_folder(tmp_path):
    for label in ("stop", "_unknown_", "_silence_"):
        (tmp_path / label).mkdir()
        (tmp_path / label / "a.wav").write_bytes(b"")
    clips = cw.official_clips(tmp_path)
    assert sorted(label for label, _ in clips) == ["_silence_", "_unknown_", "stop"]
    assert cw.outcome("_silence_", [("go", 0.9)]) == "false"


def test_a_run_at_threshold_0_is_scored_again_at_any_threshold():
    rows = [
        {"label": "go", "heard": "go", "conf": "0.95", "outcome": "right"},
        {"label": "go", "heard": "go", "conf": "0.85", "outcome": "right"},
        {"label": "stop", "heard": "stop", "conf": "0.4", "outcome": "right"},
        {"label": "bed", "heard": "stop", "conf": "0.3", "outcome": "false"},
        {"label": "cat", "heard": "", "conf": "", "outcome": "quiet"},
    ]
    at = cw.rethreshold(rows, 0.85)
    assert [r["outcome"] for r in at] == ["right", "right", "missed", "quiet", "quiet"]
    # stop at any: a stop that is the top class acts, for the stop clip and for "bed" alike
    at = cw.rethreshold(rows, 0.85, stop_any=True)
    assert [r["outcome"] for r in at] == ["right", "right", "right", "false", "quiet"]


def test_the_threshold_is_the_highest_that_keeps_ninety_percent_right():
    # ten go clips: at 0.85 nine are right (90%), at 0.9 seven
    confs = ["0.99", "0.98", "0.97", "0.96", "0.95", "0.93", "0.91", "0.88", "0.85", "0.5"]
    rows = [{"label": "go", "heard": "go", "conf": c, "outcome": "right"} for c in confs]
    assert cw.choose_threshold(rows) == 0.85  # a clip at exactly 0.85 still counts
    assert cw.choose_threshold(rows, keep=0.7) == 0.9


def test_choose_prints_the_threshold_and_what_stop_at_any_changes(tmp_path, capsys):
    import csv

    path = tmp_path / "val_kws_0_clean.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["clip", "label", "heard", "conf", "outcome", "ms"])
        w.writeheader()
        for i in range(9):
            w.writerow({"clip": f"go/{i}.wav", "label": "go", "heard": "go", "conf": "0.95"})
        w.writerow({"clip": "stop/a.wav", "label": "stop", "heard": "stop", "conf": "0.4"})
        w.writerow({"clip": "bed/a.wav", "label": "bed", "heard": "stop", "conf": "0.3"})
    assert cw.main(["--choose", str(path)]) == 0
    out = capsys.readouterr().out
    assert "threshold 0.95: 9 of 10 command clips right (90.0%)" in out
    assert "stop at 0.95: 1 of 1 stops missed, 0 heard as go; 0 of 1 other words" in out
    assert "stop at any: 0 of 1 stops missed, 0 heard as go; 1 of 1 other words" in out
