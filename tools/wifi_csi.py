"""Fit and score the Wi-Fi presence model (demo/csi.py) on recorded ESP32 CSI.

The data: the Office Localization set (Gad et al., 2026; CC BY 4.0;
huggingface.co/datasets/gadgadgad/OfficeLocalization): two ESP32-C6 boards 3 m apart in an office,
one sending, one receiving and printing esp-csi's CSI_DATA lines, about 200 packets a second. Each
file is one session of 7 to 17 minutes: the room empty, or a person in zone 1, 2 or 5. The authors'
split is by session: *_1 to train, *_2 to test, so the test is a later recording, not frames
next to the training ones. empty_3 to empty_5 are three more empty sessions, used here only to
count false alarms.

    python tools/wifi_csi.py --data datasets/office_csi --fetch        # download, about 950 MB
    python tools/wifi_csi.py --data datasets/office_csi --out demo/csi_model/wifi_csi_office.npz

It prints, for each test session, the share of its one-second windows given each label; then the
presence accuracy (empty or not), the share of windows with a person found, and the share of empty
windows called occupied. For comparison, the RSSI alone (its mean and spread over the window), which
is all a laptop or a phone can read, with two models: the same logistic regression as the CSI, and
a random forest (300 trees), the more flexible of the two, so the gap is not down to the model.
Fitting needs scikit-learn; the saved model runs with numpy alone.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo import csi  # noqa: E402

URL = "https://huggingface.co/datasets/gadgadgad/OfficeLocalization/resolve/main/{}.csv"
LABELS = ["empty", "one", "two", "five"]
NAMES = {"empty": "empty room", "one": "zone 1", "two": "zone 2", "five": "zone 5"}
EXTRA_EMPTY = ["empty_3", "empty_4", "empty_5"]


def session(path: Path):
    """Features, motion and RSSI (mean, spread) per one-second window of one file."""
    packets = list(csi.read_file(path))
    f, m = [], []
    for _, amps in csi.windows(packets):
        f.append(csi.features(amps))
        m.append(csi.motion(amps))
    # the RSSI over the same windows, as a one-value "amplitude"
    rssi = (csi.Packet(p.t, p.rssi, np.array([float(p.rssi)])) for p in packets)
    r = [[w[:, 0].mean(), w[:, 0].std()] for _, w in csi.windows(rssi)]
    return np.array(f), np.array(m), np.array(r)


def fit(train: dict, empty_motion: np.ndarray, c: float = 1.0) -> csi.ZoneModel:
    """Logistic regression on the standardised features (C fixed at 1: nothing tuned on a test
    session); the movement threshold is the 99th percentile of the first empty session."""
    from sklearn.linear_model import LogisticRegression

    X = np.vstack([train[k] for k in LABELS])
    y = np.concatenate([[i] * len(train[k]) for i, k in enumerate(LABELS)])
    mean, scale = X.mean(0), X.std(0) + 1e-9
    lr = LogisticRegression(C=c, max_iter=5000).fit((X - mean) / scale, y)
    return csi.ZoneModel(
        LABELS, mean, scale, lr.coef_, lr.intercept_, float(np.quantile(empty_motion, 0.99))
    )


def table(pred: dict, title: str) -> dict:
    print(f"\n{title}: share of the one-second windows given each label")
    print("| Session | Windows | " + " | ".join(NAMES[k] for k in LABELS) + " |")
    print("|---|---:|" + "---:|" * len(LABELS))
    right = total = pres_right = 0
    person = [0, 0]  # windows with a person: found, all
    empty = [0, 0]  # empty windows: called occupied, all
    rows = {}
    for k, p in pred.items():
        share = [float(np.mean(p == i)) for i in range(len(LABELS))]
        truth = LABELS.index(k.split("_")[0])
        right += int((p == truth).sum())
        pres_right += int(((p > 0) == (truth > 0)).sum())
        total += len(p)
        if truth > 0:
            person[0] += int((p > 0).sum())
            person[1] += len(p)
        else:
            empty[0] += int((p > 0).sum())
            empty[1] += len(p)
        rows[k] = {"windows": len(p), "share": dict(zip(LABELS, share, strict=True))}
        print(f"| {k} | {len(p)} | " + " | ".join(f"{s:.2f}" for s in share) + " |")
    found, false_alarm = person[0] / person[1], empty[0] / empty[1]
    out = {
        "zone_accuracy": right / total,
        "presence_accuracy": pres_right / total,
        # most windows are empty, which flatters the pooled figure: both rates, and their mean
        "person_found": found,
        "person_missed": person[1] - person[0],
        "empty_flagged": false_alarm,
        "empty_flagged_n": empty[0],
        "balanced_accuracy": (found + 1 - false_alarm) / 2,
        "rows": rows,
    }
    print(
        f"zone right {out['zone_accuracy']:.3f}, empty-or-not right {out['presence_accuracy']:.3f}"
        f" (person found {found:.3f}, {out['person_missed']} missed; empty called occupied"
        f" {false_alarm:.4f}, {empty[0]} windows; balanced {out['balanced_accuracy']:.3f})"
    )
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--fetch", action="store_true", help="download the files first")
    ap.add_argument("--out", type=Path, help="save the model (.npz) and the scores (.json)")
    a = ap.parse_args(argv)
    files = [f"{k}_{s}" for k in LABELS for s in (1, 2)] + EXTRA_EMPTY
    if a.fetch:
        a.data.mkdir(parents=True, exist_ok=True)
        for name in files:
            dest = a.data / f"{name}.csv"
            if not dest.exists():
                print(f"fetching {name}", flush=True)
                part = dest.with_name(dest.name + ".part")  # a cut download is not kept as done
                urllib.request.urlretrieve(URL.format(name), part)
                part.replace(dest)
    data = {}
    for name in files:
        path = a.data / f"{name}.csv"
        if path.exists():
            data[name] = session(path)
            print(f"{name}: {len(data[name][0])} windows", flush=True)
    missing = [f"{k}_{s}" for k in LABELS for s in (1, 2) if f"{k}_{s}" not in data]
    if missing:
        print(f"missing: {', '.join(missing)} (use --fetch)", file=sys.stderr)
        return 1

    model = fit({k: data[f"{k}_1"][0] for k in LABELS}, data["empty_1"][1])
    tests = [f"{k}_2" for k in LABELS] + [n for n in EXTRA_EMPTY if n in data]

    def predict(name):
        return np.array([int(np.argmax(model.scores(f))) for f in data[name][0]])

    csi_scores = table({n: predict(n) for n in tests}, "CSI, held-out sessions")

    from sklearn.ensemble import RandomForestClassifier

    Xr = np.vstack([data[f"{k}_1"][2] for k in LABELS])
    yr = np.concatenate([[i] * len(data[f"{k}_1"][2]) for i, k in enumerate(LABELS)])
    rssi_lr = fit({k: data[f"{k}_1"][2] for k in LABELS}, data["empty_1"][1])  # same as the CSI's
    rssi_lr_scores = table(
        {n: np.array([int(np.argmax(rssi_lr.scores(r))) for r in data[n][2]]) for n in tests},
        "RSSI alone, the same logistic regression",
    )
    rf = RandomForestClassifier(300, random_state=0, n_jobs=-1).fit(Xr, yr)
    rssi_scores = table({n: rf.predict(data[n][2]) for n in tests}, "RSSI alone, random forest")

    moving = {n: float(np.mean(data[n][1] > model.threshold)) for n in tests}
    print(f"\nmotion over {model.threshold:.4f} (99th percentile of empty_1):")
    for n, v in moving.items():
        print(f"  {n}: {v:.2f} of windows")

    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        model.save(a.out)
        a.out.with_suffix(".json").write_text(
            json.dumps(
                {
                    "csi": csi_scores,
                    "rssi_only": rssi_scores,  # the random forest
                    "rssi_only_logistic": rssi_lr_scores,
                    "moving": moving,
                    "threshold": model.threshold,
                    "window_s": 1.0,
                    "data": "Office Localization (Gad et al., 2026), CC BY 4.0",
                },
                indent=1,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"saved {a.out} and {a.out.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
