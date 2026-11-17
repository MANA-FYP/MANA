"""The scoring of tools/command_words.py (#46): the noise mixing, the outcome of a clip and the
summary. Vosk and Speech Commands are not needed."""

import importlib.util
import sys
import wave
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


# The stream (--stream) runs the demo's own Spotter, which needs torch (demo/kws.py).
needs_torch = pytest.mark.skipif(
    not all(importlib.util.find_spec(m) for m in ("torch", "torchaudio")),
    reason="needs torch and torchaudio",
)
SECOND = 16000


def loud(seconds):
    return np.full(int(seconds * SECOND), 8000, dtype=np.int16)


def quiet(seconds):
    return np.zeros(int(seconds * SECOND), dtype=np.int16)


def fake_score(prob, loud_share=1.0):
    """A stand-in model: "stop" at prob when loud_share of the 1 s window is loud, the rest
    shared by "unknown" and "silence"."""

    def score(wave):
        from demo.kws import LABELS

        p = np.zeros(len(LABELS))
        if np.mean(np.abs(wave) > 0.1) >= loud_share:
            p[LABELS.index("stop")] = prob
        p[LABELS.index("unknown")] = p[LABELS.index("silence")] = (1 - p.sum()) / 2
        return p

    return score


def stream(segments, prob, threshold, stop_any=False, loud_share=1.0):
    probs, pieces, spans = cw.stream_scores(segments, fake_score(prob, loud_share))
    return cw.stream_rows(probs, pieces, spans, threshold, stop_any)


@needs_torch
def test_a_word_in_the_stream_goes_to_the_clip_it_was_said_in():
    segments = [
        ("bed", Path("bed/a.wav"), np.concatenate([quiet(1), quiet(1)])),
        ("stop", Path("stop/b.wav"), np.concatenate([quiet(1), loud(1)])),
        ("cat", Path("cat/c.wav"), np.concatenate([quiet(1), quiet(1)])),
    ]
    rows, alone = stream(segments, 0.95, 0.85)
    assert [(r["clip"], r["heard"], r["outcome"]) for r in rows] == [
        ("bed/a.wav", "", "quiet"), ("stop/b.wav", "stop", "right"), ("cat/c.wav", "", "quiet"),
    ]  # fmt: skip
    assert rows[1]["extra"] == 0 and alone == []


@needs_torch
def test_a_threshold_misses_a_doubtful_stop_unless_stop_acts_at_any_probability():
    segments = [("stop", Path("stop/b.wav"), np.concatenate([quiet(1), loud(1)]))]
    assert stream(segments, 0.4, 0.85)[0][0]["outcome"] == "missed"  # the top class, at 0.4
    assert stream(segments, 0.4, 0.85, stop_any=True)[0][0]["outcome"] == "right"


@needs_torch
def test_a_word_while_no_clip_is_heard_is_counted_on_its_own():
    segments = [
        ("bed", Path("bed/a.wav"), np.concatenate([quiet(1), quiet(1)])),
        ("cat", Path("cat/c.wav"), np.concatenate([loud(1), quiet(1)])),  # a loud gap
    ]
    rows, alone = stream(segments, 0.95, 0.85)
    assert alone == ["stop"] and all(r["outcome"] == "quiet" for r in rows)


@needs_torch
def test_a_word_is_given_once_a_rearm_time_and_again_after_it():
    segments = [
        ("stop", Path("stop/b.wav"), np.concatenate([quiet(1), loud(1)])),
        ("bed", Path("bed/a.wav"), np.concatenate([quiet(1), quiet(1)])),
    ]
    # half-loud windows pass: those ending 1.5 to 2.5 s; given at 1.5 s and, 1 s on, at 2.5 s
    rows, alone = stream(segments, 0.95, 0.85, loud_share=0.5)
    assert rows[0]["outcome"] == "right" and rows[0]["extra"] == 1 and alone == []


def test_the_stream_pads_each_clip_and_puts_its_noise_in_the_gap_too(tmp_path):
    sc = tmp_path / "sc"
    (sc / "_background_noise_").mkdir(parents=True)
    (sc / "go").mkdir()
    rng = np.random.default_rng(0)
    write(sc / "_background_noise_" / "white.wav", (rng.standard_normal(320000) * 1000))
    write(sc / "go" / "a.wav", (rng.standard_normal(12000) * 3000))  # shorter than 1 s
    [(label, path, segment)] = cw.stream_sound(sc, [("go", sc / "go" / "a.wav")], 10.0, 1.0)
    assert label == "go" and segment.size == 2 * SECOND
    gap = segment[:SECOND].astype(np.float64)
    clip = cw.read_wav(path).astype(np.float64)
    assert 10 * np.log10(np.mean(clip**2) / np.mean(gap**2)) == pytest.approx(10.0, abs=0.5)


def write(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SECOND)
        w.writeframes(np.clip(samples, -32768, 32767).astype(np.int16).tobytes())
