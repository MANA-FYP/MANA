"""Sirens and horns heard on the phone's microphone (#47).

EfficientAT-mn10 (an AudioSet tagger, MIT licence, https://github.com/fschmid56/EfficientAT) scores
the last 10 s of sound once a second. It was trained on 10 s clips and gives nonsense on less, so a
shorter start is padded with silence. A siren or a horn is heard when its score passes the
threshold that gave the best F1 on UrbanSound8K's folds 1 to 5 (docs/results.md: on folds 6 to 10,
sirens F1 0.86, horns F1 0.74). The model's code and weights are not in this repository: --sounds
gives the folder of an EfficientAT checkout with resources/mn10_as_mAP_471.pt in it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

RATE = 32000  # EfficientAT's sample rate
WINDOW_S = 10.0  # what it was trained on
EVERY_S = 1.0  # how often the window is scored
# AudioSet's classes (class_labels_indices.csv): Siren, Civil defense siren, Emergency vehicle,
# Police car (siren), Ambulance (siren), Fire engine (siren); Vehicle horn, Toot, Air horn.
CLASSES = {"siren": (396, 397, 322, 323, 324, 325), "horn": (308, 309, 318)}
THRESHOLDS = {"siren": 0.22, "horn": 0.08}  # best F1 on UrbanSound8K folds 1 to 5


def resample(samples: np.ndarray, rate: int, to: int = RATE) -> np.ndarray:
    """Linear interpolation: enough for a tagger that looks at mel bands up to a few kHz."""
    if rate == to or samples.size == 0:
        return samples.astype(np.float32)
    n = int(round(samples.size * to / rate))
    return np.interp(np.arange(n) * rate / to, np.arange(samples.size), samples).astype(np.float32)


class Listener:
    """Keeps the last WINDOW_S of sound and says when it is time to score it again."""

    def __init__(self, score, every_s: float = EVERY_S):
        self.score = score  # 10 s of sound at RATE -> one score per AudioSet class
        self.every_s = every_s
        self.sound = np.zeros(0, dtype=np.float32)
        self.scored_at: float | None = None

    def feed(self, samples: np.ndarray, rate: int) -> None:
        keep = int(WINDOW_S * RATE)
        self.sound = np.concatenate([self.sound, resample(samples, rate)])[-keep:]

    def due(self, now: float) -> bool:
        return self.sound.size > 0 and (
            self.scored_at is None or now - self.scored_at >= self.every_s
        )

    def window(self) -> np.ndarray:
        keep = int(WINDOW_S * RATE)
        return (
            np.pad(self.sound, (0, keep - self.sound.size))
            if self.sound.size < keep
            else self.sound
        )

    def hear(self, now: float) -> dict[str, float]:
        """The hazards heard in the last WINDOW_S, with their scores. Blocking: run it off the
        loop."""
        self.scored_at = now
        scores = self.score(self.window())
        out = {}
        for hazard, classes in CLASSES.items():
            s = float(np.max(scores[list(classes)]))
            if s >= THRESHOLDS[hazard]:
                out[hazard] = round(s, 2)
        return out


def load(folder: str | Path):
    """EfficientAT-mn10 from a checkout: a function of 10 s of sound to 527 class scores."""
    import torch

    folder = str(Path(folder).resolve())
    sys.path.insert(0, folder)
    cwd = os.getcwd()
    os.chdir(folder)  # it finds resources/mn10_as_mAP_471.pt there, and downloads nothing
    try:
        from models.mn.model import get_model
        from models.preprocess import AugmentMelSTFT

        model = get_model(width_mult=1.0, pretrained_name="mn10_as").eval()
    finally:
        os.chdir(cwd)
    mel = AugmentMelSTFT(n_mels=128, sr=RATE, win_length=800, hopsize=320).eval()

    def score(wave: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            spec = mel(torch.from_numpy(wave[None, :].astype(np.float32)))
            logits, _ = model(spec.unsqueeze(0))
            return torch.sigmoid(logits.float()).numpy()[0]

    return score
