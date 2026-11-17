"""Sirens and horns: EfficientAT-mn10 against the YAMNet baseline on UrbanSound8K (#47).

    python tools/sound_hazards.py --us8k datasets/UrbanSound8K \
        --efficientat <EfficientAT checkout, with resources/mn10_as_mAP_471.pt> \
        --yamnet <folder with yamnet.py, params.py, features.py, yamnet_class_map.csv, yamnet.h5>

Both are AudioSet taggers, used as they come (no training here). A clip's siren score is the highest
score of AudioSet's siren classes (below) over the clip, its horn score the same for horns.
UrbanSound8K's `siren` and `car_horn` clips are the positives, every other clip a negative. The
threshold of each is chosen on folds 1 to 5 (the best F1) and precision and recall are reported on
folds 6 to 10, so the threshold is not fitted to the clips it is scored on; average precision needs
no threshold. The time per clip on this CPU is kept too: the model has to keep up with a microphone.

Needs (not in the repository's requirements): torch, torchvision, torchaudio and librosa for
EfficientAT; tensorflow, tf-keras and resampy for YAMNet. The scores are kept in
<out>/scores_<model>.csv; --again computes them anew.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

# AudioSet's classes (class_labels_indices.csv, 527) and YAMNet's (yamnet_class_map.csv, 521: six
# speech and music classes dropped, so the same class sits 6 lower). "Emergency vehicle" is the
# parent of the three siren kinds; "Toot" is a child of the vehicle horn
SIRENS = {
    "efficientat": (396, 397, 322, 323, 324, 325),  # Siren, Civil defense siren, Emergency vehicle,
    "yamnet": (390, 391, 316, 317, 318, 319),  # Police car, Ambulance, Fire engine (siren)
}
HORNS = {
    "efficientat": (308, 309, 318),  # Vehicle horn, car horn, honking; Toot; Air horn, truck horn
    "yamnet": (302, 303, 312),
}
POSITIVE = {"siren": "siren", "horn": "car_horn"}  # UrbanSound8K's class for each
TUNE_FOLDS, TEST_FOLDS = range(1, 6), range(6, 11)


def clip_score(scores: np.ndarray, classes) -> float:
    """The highest score of any of these classes over the clip (frames x classes, or one row)."""
    scores = np.asarray(scores)
    return float(np.max(scores.reshape(-1, scores.shape[-1])[:, list(classes)]))


def counts(scores, labels, threshold: float) -> tuple[int, int, int]:
    """True positives, false positives and false negatives at this threshold."""
    hit = np.asarray(scores) >= threshold
    labels = np.asarray(labels, bool)
    return int((hit & labels).sum()), int((hit & ~labels).sum()), int((~hit & labels).sum())


def precision_recall(scores, labels, threshold: float) -> tuple[float, float, float]:
    tp, fp, fn = counts(scores, labels, threshold)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def best_threshold(scores, labels) -> float:
    """The threshold, among the scores themselves, with the highest F1."""
    candidates = np.unique(np.asarray(scores))
    return float(max(candidates, key=lambda t: precision_recall(scores, labels, t)[2]))


def average_precision(scores, labels) -> float:
    """The mean of the precision at the rank of each positive, most confident first."""
    order = np.argsort(-np.asarray(scores), kind="stable")
    hits = np.asarray(labels, bool)[order]
    if not hits.any():
        return 0.0
    precision_at = np.cumsum(hits) / np.arange(1, hits.size + 1)
    return float(precision_at[hits].mean())


# ---- the models (only when run): each returns a function from a mono waveform at its own rate
# to the scores of every AudioSet class, and that rate


def ten_seconds(wave: np.ndarray, rate: int) -> np.ndarray:
    """EfficientAT was trained on AudioSet's 10 s clips (input_dim_t=1000 frames). A shorter clip
    as it is gives nonsense: on UrbanSound8K every clip under 2 s scored over 0.9 for both siren
    and horn. So it is padded with silence to 10 s, as AudioSet's shorter clips were."""
    return wave if wave.size >= 10 * rate else np.pad(wave, (0, 10 * rate - wave.size))


def efficientat(folder: Path):
    import os

    import torch

    sys.path.insert(0, str(folder))
    cwd = os.getcwd()
    os.chdir(folder)  # it finds resources/mn10_as_mAP_471.pt there, and downloads nothing
    try:
        from models.mn.model import get_model
        from models.preprocess import AugmentMelSTFT

        model = get_model(width_mult=1.0, pretrained_name="mn10_as").eval()
    finally:
        os.chdir(cwd)
    mel = AugmentMelSTFT(n_mels=128, sr=32000, win_length=800, hopsize=320).eval()

    def run(wave: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            wave = ten_seconds(wave, 32000)
            spec = mel(torch.from_numpy(wave[None, :].astype(np.float32)))
            logits, _ = model(spec.unsqueeze(0))
            return torch.sigmoid(logits.float()).numpy()[0]

    return run, 32000


def yamnet(folder: Path):
    sys.path.insert(0, str(folder))
    import params as yamnet_params
    import yamnet as yamnet_model

    p = yamnet_params.Params()
    model = yamnet_model.yamnet_frames_model(p)
    model.load_weights(str(folder / "yamnet.h5"))

    def run(wave: np.ndarray) -> np.ndarray:
        if wave.size < 16000:  # it needs about 0.975 s for its first frame
            wave = np.pad(wave, (0, 16000 - wave.size))
        scores, _, _ = model(wave.astype(np.float32))
        return np.asarray(scores)

    return run, 16000


def clips(us8k: Path):
    """(path, fold, class) of every clip, from the dataset's own metadata."""
    with (us8k / "metadata" / "UrbanSound8K.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            yield (
                us8k / "audio" / f"fold{r['fold']}" / r["slice_file_name"],
                int(r["fold"]),
                r["class"],
            )


def score_all(us8k: Path, name: str, folder: Path, out: Path) -> Path:
    import librosa

    run, rate = (efficientat if name == "efficientat" else yamnet)(folder)
    path = out / f"scores_{name}.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["clip", "fold", "class", "siren", "horn", "seconds", "ms"])
        for n, (clip, fold, kind) in enumerate(clips(us8k), 1):
            wave, _ = librosa.load(clip, sr=rate, mono=True)
            start = time.perf_counter()
            s = run(wave)
            ms = (time.perf_counter() - start) * 1000
            siren, horn = clip_score(s, SIRENS[name]), clip_score(s, HORNS[name])
            w.writerow([clip.name, fold, kind, f"{siren:.5f}", f"{horn:.5f}",
                        f"{wave.size / rate:.2f}", f"{ms:.1f}"])  # fmt: skip
            if n % 500 == 0:
                print(f"{name}: {n} clips", flush=True)
    return path


def load(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def report(name: str, rows: list[dict]) -> list[str]:
    lines = []
    tune = [r for r in rows if int(r["fold"]) in TUNE_FOLDS]
    test = [r for r in rows if int(r["fold"]) in TEST_FOLDS]
    for hazard, kind in POSITIVE.items():
        ts, ty = (
            np.array([float(r[hazard]) for r in tune]),
            np.array([r["class"] == kind for r in tune]),
        )
        s, y = (
            np.array([float(r[hazard]) for r in test]),
            np.array([r["class"] == kind for r in test]),
        )
        t = best_threshold(ts, ty)
        p, r, f1 = precision_recall(s, y, t)
        tp, fp, fn = counts(s, y, t)
        p5, r5, _ = precision_recall(s, y, 0.5)
        lines.append(
            f"| {name} | {hazard} | {int(y.sum())} | {t:.2f} | {p:.0%} | {r:.0%} | {f1:.2f} "
            f"| {tp} / {fp} / {fn} | {p5:.0%} / {r5:.0%} | {average_precision(s, y):.2f} |"
        )
    return lines


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--us8k", type=Path, default=Path("datasets/UrbanSound8K"))
    p.add_argument("--efficientat", type=Path, help="the EfficientAT checkout")
    p.add_argument("--yamnet", type=Path, help="the folder with YAMNet's code and weights")
    p.add_argument("--out", type=Path, default=None, help="where the scores go (default: --us8k)")
    p.add_argument("--again", action="store_true", help="score the clips again")
    a = p.parse_args(argv)
    out = a.out or a.us8k
    print("| Model | Hazard | Positives (folds 6-10) | Threshold (folds 1-5) | Precision | Recall "
          "| F1 | TP / FP / FN | At 0.5: precision / recall | Average precision |")  # fmt: skip
    print("|---|---|---|---|---|---|---|---|---|---|")
    times = []
    for name, folder in (("efficientat", a.efficientat), ("yamnet", a.yamnet)):
        path = out / f"scores_{name}.csv"
        if a.again or not path.exists():
            if folder is None:
                continue
            score_all(a.us8k, name, folder, out)
        rows = load(path)
        print("\n".join(report(name, rows)))
        ms = np.array([float(r["ms"]) for r in rows])
        sec = np.array([float(r["seconds"]) for r in rows])
        times.append(
            f"{name}: median {np.median(ms):.0f} ms a clip ({np.median(sec):.1f} s of sound)"
        )
    print("\n" + "\n".join(times))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
