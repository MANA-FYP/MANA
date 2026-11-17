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
