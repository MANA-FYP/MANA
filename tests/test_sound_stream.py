"""tools/sound_stream.py (#47): sirens and horns on a running stream, without the model."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import sound_stream as ss  # noqa: E402

from demo.sounds import CLASSES, RATE  # noqa: E402


def test_a_folds_order_is_shuffled_and_the_same_each_time():
    order = ss.stream_order(50, 3)
    assert sorted(order) == list(range(50)) and order != list(range(50))
    assert ss.stream_order(50, 3) == order and ss.stream_order(50, 4) != order


def test_the_clips_run_on_into_each_other_in_pieces_of_one_size():
    pieces = list(ss.chunks([np.ones(5), np.zeros(4), np.ones(2)], 4))
    assert [p.size for p in pieces] == [4, 4, 3]
    assert np.concatenate(pieces).tolist() == [1] * 5 + [0] * 4 + [1] * 2


def test_each_second_the_demos_window_of_the_last_10_s_is_scored():
    seen = []

    def score(window):
        seen.append(window.copy())
        out = np.zeros(527)
        out[CLASSES["siren"][0]] = window.max()  # loud is a siren here
        return out

    quiet, loud = np.zeros(3 * RATE), np.full(RATE, 0.5)
    rows = list(ss.listen([quiet, loud, np.zeros(11 * RATE)], score))
    assert [t for t, *_ in rows] == [float(n) for n in range(1, 16)]
    assert all(w.size == 10 * RATE for w in seen)  # padded at the start, as the demo pads it
    sirens = [s for _, s, _, _ in rows]
    assert sirens[:3] == [0.0] * 3 and sirens[3:13] == [0.5] * 10  # in the window for 10 s
    assert sirens[13:] == [0.0] * 2


def test_events_join_positive_clips_that_follow_each_other():
    spans = [(0, 3, "siren"), (3, 7, "siren"), (7, 8, "dog_bark"), (8, 9, "siren")]
    assert ss.events(spans, "siren") == [(0, 3 + 4), (8, 9)]
    assert ss.events(spans, "car_horn") == []


def test_a_window_holds_an_event_while_any_of_it_is_in_the_last_10_s():
    evs = [(20.0, 24.0)]
    assert not ss.holds(evs, 20.0)  # the window ends where it starts
    assert ss.holds(evs, 21.0) and ss.holds(evs, 33.0)
    assert not ss.holds(evs, 34.0)  # it ended 10 s ago


def test_a_line_is_not_said_again_within_10_s_of_the_last_time_it_was_said():
    assert ss.said([1, 2, 5, 11, 12, 30]) == [1, 11, 30]


def test_events_heard_and_alerts_right_or_false():
    evs = [(10.0, 14.0), (100.0, 102.0)]
    windows = [(float(t), 0.0) for t in range(1, 201)]
    for t in (12, 13, 50, 150, 151):
        windows[t - 1] = (float(t), 0.9)
    r = ss.evaluate(windows, evs, 0.5, 200.0)
    assert (r["events"], r["heard"], r["delays"]) == (2, 1, [2.0])  # the second is missed
    assert (r["alerts"], r["right"], r["false"]) == (3, 1, 2)  # 13 is said with 12; 151 with 150
    assert r["hours"] == pytest.approx(200 / 3600)


def test_the_threshold_for_a_recall_is_the_highest_that_reaches_it():
    evs = [(float(30 * i), float(30 * i + 2)) for i in range(1, 21)]  # far apart
    windows = [(float(30 * i + 1), 0.5 if i == 1 else 0.8) for i in range(1, 21)]
    assert ss.threshold_for_recall([(windows, evs, 700.0)], 0.95) == pytest.approx(0.8)
    assert ss.threshold_for_recall([(windows, evs, 700.0)], 1.0) == pytest.approx(0.5)
