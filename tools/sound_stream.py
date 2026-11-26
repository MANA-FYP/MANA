"""Sirens and horns on a running stream, as the demo hears them (#47).

    python tools/sound_stream.py --us8k datasets/UrbanSound8K \
        --efficientat <EfficientAT checkout, with resources/mn10_as_mAP_471.pt> --out out

tools/sound_hazards.py scores each UrbanSound8K clip on its own, padded with silence to 10 s. The
demo does not hear clips: demo.sounds.Listener keeps the last 10 s of the phone's microphone and
scores it once a second. So here the clips of each fold are put one after another, in a shuffled
order (seed: the fold), into one stream at 32 kHz, the rate the phone sends. The stream is fed to
the demo's own Listener 1 s at a time, and its window is scored by EfficientAT-mn10 each second.

What is counted, for sirens and for horns:
- An event is a run of positive clips next to each other in the stream. It is heard when a window
  that holds part of it (scored after its start, up to 10 s after its end) passes the threshold.
- An alert is what the demo would say: a window over the threshold, unless the same line was said
  in the last 10 s (demo.middleware's rule). An alert is right when its window holds some of that
  hazard, and false when it holds none.
- Thresholds: the demo's (from clips, folds 1 to 5), and the highest one that hears 95% of the
  events in the streams of folds 1 to 5. Both are scored on folds 6 to 10.

Needs (not in the repository's requirements): torch, torchvision, torchaudio and librosa. The
scores of each window are kept in <out>/stream_<fold>.csv and the clips' places in
<out>/spans_<fold>.csv; --again computes them anew.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.sounds import CLASSES, EVERY_S, RATE, THRESHOLDS, WINDOW_S, Listener  # noqa: E402

POSITIVE = {"siren": "siren", "horn": "car_horn"}  # UrbanSound8K's class for each
TUNE_FOLDS, TEST_FOLDS = range(1, 6), range(6, 11)
TARGET_RECALL = 0.95
REPEAT_S = 10.0  # demo.middleware says the same line again only after 10 s


def stream_order(n: int, fold: int) -> list[int]:
    """The order of a fold's clips in its stream: shuffled, the same each time."""
    return [int(i) for i in np.random.default_rng(fold).permutation(n)]


def chunks(waves, size: int):
    """The clips one after another, cut into pieces of `size` samples (the last one shorter)."""
    rest = np.zeros(0, dtype=np.float32)
    for wave in waves:
        rest = np.concatenate([rest, np.asarray(wave, dtype=np.float32)])
        while rest.size >= size:
            yield rest[:size]
            rest = rest[size:]
    if rest.size:
        yield rest


def listen(waves, score, rate: int = RATE):
    """Feed the stream to the demo's Listener, EVERY_S at a time, and score its window each time.
    Yields (t, siren score, horn score, ms): t is the stream's time at the window's end."""
    listener = Listener(score)
    t = 0.0
    for piece in chunks(waves, int(EVERY_S * rate)):
        listener.feed(piece, rate)
        t += piece.size / rate
        start = time.perf_counter()
        scores = np.asarray(listener.score(listener.window()))
        ms = (time.perf_counter() - start) * 1000
        yield (
            round(t, 3),
            *(float(np.max(scores[list(CLASSES[h])])) for h in ("siren", "horn")),
            ms,
        )


def events(spans: list[tuple[float, float, str]], kind: str) -> list[tuple[float, float]]:
    """(start, end) of each run of clips of this kind next to each other in the stream."""
    out: list[list[float]] = []
    for start, end, k in spans:
        if k != kind:
            continue
        if out and abs(out[-1][1] - start) < 1e-6:
            out[-1][1] = end
        else:
            out.append([start, end])
    return [(s, e) for s, e in out]


def holds(evs: list[tuple[float, float]], t: float, window_s: float = WINDOW_S) -> bool:
    """Whether the window ending at t holds part of any of these events."""
    return any(s < t and e > t - window_s for s, e in evs)


def said(times, repeat_s: float = REPEAT_S) -> list[float]:
    """The times a line is said, when it is heard at these times: not again within repeat_s of
    the last time it was said."""
    out: list[float] = []
    for t in times:
        if not out or t - out[-1] >= repeat_s:
            out.append(t)
    return out


def evaluate(windows, evs, threshold: float, length_s: float) -> dict:
    """Events heard and alerts said, for one hazard on one stream.
    windows: (t, score) of each scored window; evs: the hazard's events in the stream."""
    fired = [t for t, s in windows if s >= threshold]
    heard, delays = 0, []
    for s, e in evs:
        hits = [t for t in fired if s < t <= e + WINDOW_S]
        if hits:
            heard += 1
            delays.append(hits[0] - s)
    alerts = said(fired)
    right = sum(holds(evs, t) for t in alerts)
    return {
        "events": len(evs),
        "heard": heard,
        "delays": delays,
        "alerts": len(alerts),
        "right": right,
        "false": len(alerts) - right,
        "hours": length_s / 3600,
    }


def combine(results: list[dict]) -> dict:
    out = {k: sum(r[k] for r in results) for k in ("events", "heard", "alerts", "right", "false")}
    out["hours"] = sum(r["hours"] for r in results)
    out["delays"] = [d for r in results for d in r["delays"]]
    return out


def threshold_for_recall(per_stream, target: float = TARGET_RECALL) -> float:
    """The highest threshold, in steps of 0.01, that hears at least `target` of the events.
    per_stream: (windows, events, length) of each stream."""
    for t in (n / 100 for n in range(99, 0, -1)):
        r = combine([evaluate(w, e, t, n) for w, e, n in per_stream])
        if r["events"] and r["heard"] / r["events"] >= target:
            return t
    return 0.01


# ---- the model and the clips (only when run)


def clips(us8k: Path, fold: int) -> list[tuple[Path, str]]:
    """(path, class) of the fold's clips, from the dataset's own metadata, in its order."""
    with (us8k / "metadata" / "UrbanSound8K.csv").open(newline="") as f:
        return [
            (us8k / "audio" / f"fold{r['fold']}" / r["slice_file_name"], r["class"])
            for r in csv.DictReader(f)
            if int(r["fold"]) == fold
        ]


def score_fold(us8k: Path, fold: int, score, out: Path) -> None:
    import librosa

    fold_clips = [clips(us8k, fold)[i] for i in stream_order(len(clips(us8k, fold)), fold)]
    spans = []

    def waves():
        at = 0.0
        for path, kind in fold_clips:
            wave, _ = librosa.load(path, sr=RATE, mono=True)
            spans.append((path.name, kind, at, at + wave.size / RATE))
            at += wave.size / RATE
            yield wave

    with (out / f"stream_{fold}.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "siren", "horn", "ms"])
        for n, (t, siren, horn, ms) in enumerate(listen(waves(), score), 1):
            w.writerow([f"{t:.3f}", f"{siren:.5f}", f"{horn:.5f}", f"{ms:.1f}"])
            if n % 600 == 0:
                print(f"fold {fold}: {n} windows", flush=True)
    with (out / f"spans_{fold}.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["clip", "class", "start", "end"])
        w.writerows([c, k, f"{s:.4f}", f"{e:.4f}"] for c, k, s, e in spans)


def load_fold(out: Path, fold: int):
    with (out / f"stream_{fold}.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    with (out / f"spans_{fold}.csv").open(newline="") as f:
        spans = [(float(r["start"]), float(r["end"]), r["class"]) for r in csv.DictReader(f)]
    return rows, spans


def line(hazard: str, why: str, folds: str, threshold: float, r: dict) -> str:
    recall = r["heard"] / r["events"] if r["events"] else 0.0
    precision = r["right"] / r["alerts"] if r["alerts"] else 0.0
    delay = f"{np.median(r['delays']):.0f} s" if r["delays"] else "-"
    return (
        f"| {hazard} | {why} | {threshold:.2f} | {folds} "
        f"| {r['heard']} of {r['events']} ({recall:.0%}) | {delay} | {r['alerts']} "
        f"| {precision:.0%} | {r['false']} | {r['false'] / r['hours']:.1f} |"
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--us8k", type=Path, default=Path("datasets/UrbanSound8K"))
    p.add_argument("--efficientat", type=Path, help="the EfficientAT checkout")
    p.add_argument("--out", type=Path, default=Path("out"))
    p.add_argument("--folds", type=int, nargs="*", default=list(range(1, 11)))
    p.add_argument("--again", action="store_true", help="score the streams again")
    a = p.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    score = None
    for fold in a.folds:
        if a.again or not (a.out / f"stream_{fold}.csv").exists():
            if score is None:
                from demo.sounds import load

                score = load(a.efficientat)
            score_fold(a.us8k, fold, score, a.out)
    data = {fold: load_fold(a.out, fold) for fold in a.folds}
    hours = sum(float(rows[-1]["t"]) for rows, _ in data.values()) / 3600
    ms = np.array([float(r["ms"]) for rows, _ in data.values() for r in rows])
    p95 = np.percentile(ms, 95)
    print(f"{len(ms)} windows, {hours:.2f} h of sound in {len(data)} folds; median "
          f"{np.median(ms):.0f} ms a window, 95th percentile {p95:.0f} ms\n")  # fmt: skip
    print("| Hazard | Threshold from | Threshold | Folds | Events heard | Median delay "
          "| Alerts said | Alerts right | False alerts | False alerts an hour |")  # fmt: skip
    print("|---|---|---|---|---|---|---|---|---|---|")

    def streams(hazard, folds):
        return [
            (
                [(float(r["t"]), float(r[hazard])) for r in rows],
                events(spans, POSITIVE[hazard]),
                float(rows[-1]["t"]),
            )
            for fold, (rows, spans) in data.items()
            if fold in folds
        ]

    for hazard in ("siren", "horn"):
        tune, test = streams(hazard, TUNE_FOLDS), streams(hazard, TEST_FOLDS)
        chosen = [(THRESHOLDS[hazard], "the demo (clips, folds 1-5)")]
        if tune:
            why = f"{TARGET_RECALL:.0%} of events (stream, folds 1-5)"
            chosen.append((threshold_for_recall(tune), why))
        for threshold, why in chosen:
            for name, group in (("1-5", tune), ("6-10", test)):
                if group:
                    r = combine([evaluate(w, e, threshold, n) for w, e, n in group])
                    print(line(hazard, why, name, threshold, r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
