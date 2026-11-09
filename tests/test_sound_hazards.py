"""tools/sound_hazards.py (#47): scoring sirens and horns, without the models."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import sound_hazards as sh  # noqa: E402


def test_a_clip_scores_its_loudest_frame_of_any_of_the_classes():
    frames = np.zeros((3, 527))
    frames[1, 396] = 0.7  # a siren in the second frame
    frames[2, 324] = 0.9  # an ambulance's siren in the third
    frames[0, 0] = 1.0  # speech: not a siren
    assert sh.clip_score(frames, sh.SIRENS["efficientat"]) == pytest.approx(0.9)
    assert sh.clip_score(frames[0], sh.SIRENS["efficientat"]) == 0.0  # one row (EfficientAT)


def test_yamnets_classes_are_audiosets_six_lower():
    for table in (sh.SIRENS, sh.HORNS):
        assert [i - 6 for i in table["efficientat"]] == list(table["yamnet"])


def test_precision_recall_and_their_counts():
    scores = [0.9, 0.8, 0.6, 0.4, 0.2]
    labels = [True, False, True, True, False]
    assert sh.counts(scores, labels, 0.5) == (2, 1, 1)
    p, r, f1 = sh.precision_recall(scores, labels, 0.5)
    assert (p, r) == (pytest.approx(2 / 3), pytest.approx(2 / 3)) and f1 == pytest.approx(2 / 3)
    assert sh.precision_recall(scores, [False] * 5, 0.95) == (0.0, 0.0, 0.0)


def test_the_threshold_is_the_one_with_the_best_f1():
    scores = [0.9, 0.8, 0.6, 0.4, 0.2]
    labels = [True, False, True, True, False]
    assert sh.best_threshold(scores, labels) == pytest.approx(0.4)  # all 3 found, 1 wrong: F1 0.86


def test_average_precision():
    assert sh.average_precision([0.9, 0.8, 0.1], [True, True, False]) == pytest.approx(1.0)
    # positives at ranks 1 and 3: (1/1 + 2/3) / 2
    assert sh.average_precision([0.9, 0.8, 0.7], [True, False, True]) == pytest.approx(5 / 6)
    assert sh.average_precision([0.5], [False]) == 0.0
