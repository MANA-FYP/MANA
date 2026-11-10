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
        {"label": "stop", "outcome": "right"},
        {"label": "go", "outcome": "missed"},
        {"label": "bed", "outcome": "false"},
        {"label": "cat", "outcome": "quiet"},
    ]
    assert cw.summary(rows) == {
        "said": 2, "right": 1, "wrong": 0, "missed": 1, "other": 2, "false": 1,
    }  # fmt: skip
