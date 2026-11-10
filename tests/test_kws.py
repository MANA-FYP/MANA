"""BC-ResNet for the command words (#46): the model's size against the paper, its input and
output, and the training data helpers on a tiny made-up Speech Commands folder."""

import sys
import wave
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("torchaudio")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import train_commands as tc  # noqa: E402

from demo import kws  # noqa: E402


@pytest.mark.parametrize(("tau", "params"), [(1, 9_232), (8, 321_068)])
def test_the_model_has_the_papers_size(tau, params):
    # the paper: BC-ResNet-1 9.2k parameters, BC-ResNet-8 321k
    assert sum(p.numel() for p in kws.BCResNet(tau).parameters()) == params


def test_one_second_of_sound_gives_a_score_for_each_of_the_twelve_classes():
    x = kws.mel()(torch.zeros(2, kws.RATE)).unsqueeze(1)
    assert tuple(x.shape) == (2, 1, kws.N_MELS, 101)
    assert tuple(kws.BCResNet(1).eval()(x).shape) == (2, len(kws.LABELS))
    assert kws.LABELS[:10] == (
        "stop",
        "go",
        "yes",
        "no",
        "left",
        "right",
        "up",
        "down",
        "on",
        "off",
    )


def write_wav(path: Path, n: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.ones(n) * 100).astype(np.int16).tobytes())


def test_the_splits_follow_the_lists_and_other_words_are_unknown(tmp_path):
    for rel in ("stop/a.wav", "stop/b.wav", "bed/c.wav", "go/d.wav", "_background_noise_/n.wav"):
        write_wav(tmp_path / rel)
    (tmp_path / "validation_list.txt").write_text("stop/b.wav\n")
    (tmp_path / "testing_list.txt").write_text("go/d.wav\n")
    s = tc.splits(tmp_path)
    as_names = {k: [(c, p.parent.name + "/" + p.name) for c, p in v] for k, v in s.items()}
    assert as_names["train"] == [(tc.UNKNOWN, "bed/c.wav"), (0, "stop/a.wav")]
    assert as_names["val"] == [(0, "stop/b.wav")]
    assert as_names["test"] == [(1, "go/d.wav")]


def test_a_clip_is_made_exactly_one_second():
    assert tc.fixed(np.ones(8000, np.int16)).size == 16000
    assert tc.fixed(np.ones(20000, np.int16)).size == 16000


def test_augmented_clips_and_silences_stay_one_second_and_in_range():
    import random

    aug = tc.Augment([(np.random.default_rng(0).standard_normal(48000) * 3000).astype(np.int16)],
                     random.Random(0))  # fmt: skip
    clip = np.full(16000, 0.1, np.float32)
    for _ in range(20):
        out = aug(clip.copy())
        assert out.shape == (16000,) and np.isfinite(out).all()
        assert aug.silence().shape == (16000,)


def probs(word: str | None, p: float = 0.95) -> np.ndarray:
    out = np.zeros(len(kws.LABELS), np.float32)
    out[kws.LABELS.index(word or "silence")] = p if word else 1.0
    return out


def pcm(seconds: float) -> bytes:
    return np.zeros(int(seconds * kws.RATE), "<i2").tobytes()


def test_the_spotter_scores_the_last_second_every_quarter_second():
    calls = []
    s = kws.Spotter(lambda w: calls.append(w.size) or probs(None))
    assert s.feed(pcm(0.9)) == [] and calls == []  # not a full second yet
    s.feed(pcm(0.1))
    s.feed(pcm(0.5))
    assert calls == [16000, 16000]  # at 1.0 s, then once for the next 0.5 s: its newest second


def test_a_word_is_given_once_while_it_lasts_and_again_after_rearm():
    s = kws.Spotter(lambda w: probs("stop"))
    heard = [w for _ in range(12) for w in s.feed(pcm(0.25))]  # 3 s of "stop"
    assert [w for w, _ in heard] == ["stop", "stop", "stop"]  # once a second, not every hop


def test_a_word_below_the_threshold_or_not_a_command_is_not_given():
    assert kws.Spotter(lambda w: probs("go", 0.6), threshold=0.9).feed(pcm(1.0)) == []
    assert kws.Spotter(lambda w: probs("unknown", 0.99)).feed(pcm(1.0)) == []


def test_a_checkpoint_that_cannot_be_read_gives_no_spotter(tmp_path):
    assert kws.Spotter.load(str(tmp_path / "missing.pt")) is None
