"""Offline command words on Speech Commands v2 (#46): the demo's Vosk recogniser, or BC-ResNet.

    python tools/command_words.py --sc datasets/speech_commands --vosk vosk-model-small-en-us-0.15
    python tools/command_words.py --sc datasets/speech_commands --kws kws8/model.pt --threshold 0.9
    python tools/command_words.py --choose val_kws_0_clean.csv val_kws_0_snr10.csv

The threshold is chosen on the validation list (--list validation_list.txt at threshold 0, then
--choose): the highest that keeps 90% of the clean command clips right, and what letting "stop"
act at any probability (--stop-any) changes for stop.

The recogniser is the demo's own (demo/speech.py): Vosk limited to the ten command words, with
"[unk]" for anything else. Each clip of the test list (testing_list.txt) is given as the demo gives
the phone's sound, 16 kHz 16-bit, followed by 0.3 s of silence, and the first command word it
returns is the answer.
- A clip of one of the ten words is right, wrong (another command word) or missed (none).
- A clip of any other word (bed, bird, forward, ...) that returns a command word is a false
  activation: the chair would act on something the user did not say to it.

Each condition is run clean and with Speech Commands' own background noise (_background_noise_:
white and pink noise, a running tap, an exercise bike, dishes, a cat) mixed in at a given
signal-to-noise ratio, a random stretch of a random noise file per clip, with a fixed seed. The
stretch comes from the last quarter of each file, which tools/train_commands.py never mixes in,
so a trained model is not tested on noise it was trained with (Waqas, #185).

The clips: the dataset's testing_list.txt, or with --official the authors' separate test set
(speech_commands_test_set_v0.02: the ten words, _unknown_ and _silence_, 1 s each). Its silence
clips measure false activations from noise alone, as when the device listens all day; they are
counted on their own, not as other words.

Stop gets its own line: a missed stop is the unsafe case, and "stop heard as go" the worst one.
With --held-out WORDS, the false activations on those words are given apart: words a trained model
never saw (tools/train_commands.py --hold-out), against the ones it was trained to reject.

The clips are aligned 1 s recordings with the word in them; the demo's Spotter listens every
0.25 s to a running stream, which these numbers do not cover.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.speech import COMMAND_WORDS, TARGET_RATE, grammar, parse_result  # noqa: E402

SILENCE = np.zeros(int(0.3 * TARGET_RATE), dtype=np.int16)
NOISE_TEST_SHARE = 0.25  # the last quarter of each noise file: for tests only, never in training


def noise_part(noise: np.ndarray, part: str) -> np.ndarray:
    """The training or the test part of a background noise file (#185: none of it in both)."""
    cut = int(noise.size * (1 - NOISE_TEST_SHARE))
    return noise[:cut] if part == "train" else noise[cut:]


def read_wav(path: Path) -> np.ndarray:
    """A Speech Commands clip: 16 kHz, 16-bit, mono."""
    with wave.open(str(path)) as w:
        assert w.getframerate() == TARGET_RATE and w.getsampwidth() == 2 and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def test_clips(sc: Path, listed: str = "testing_list.txt") -> list[tuple[str, Path]]:
    """(label, path) of every clip in the test list (or the validation list, to choose a
    threshold on clips the result is not reported on)."""
    lines = (sc / listed).read_text().split()
    return [(line.split("/")[0], sc / line) for line in lines]


def official_clips(folder: Path) -> list[tuple[str, Path]]:
    """(label, path) of the authors' test set: a folder per command word, _unknown_ and _silence_.
    Unknown clips are labelled "_unknown_" (other words) and silences "_silence_" (noise alone)."""
    return [(wav.parent.name, wav) for wav in sorted(folder.glob("*/*.wav"))]


def mix(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """speech with noise added so that the speech's power is snr_db above the noise's."""
    s = speech.astype(np.float64)
    n = noise[: s.size].astype(np.float64)
    ps, pn = np.mean(s**2), np.mean(n**2)
    if pn == 0 or ps == 0:
        return speech
    n *= np.sqrt(ps / (pn * 10 ** (snr_db / 10)))
    return np.clip(np.round(s + n), -32768, 32767).astype(np.int16)


def outcome(label: str, heard: list[tuple[str, float]]) -> str:
    """right, wrong, missed (a command word was said), or false / quiet (another word was)."""
    word = heard[0][0] if heard else None
    if label in COMMAND_WORDS:
        return "missed" if word is None else "right" if word == label else "wrong"
    return "quiet" if word is None else "false"


def summary(rows: list[dict], held_out: frozenset = frozenset()) -> dict:
    said = [r for r in rows if r["label"] in COMMAND_WORDS]
    # other spoken words; noise alone (the official set's _silence_) is counted on its own
    other = [r for r in rows if r["label"] not in COMMAND_WORDS and r["label"] != "_silence_"]
    silence = [r for r in rows if r["label"] == "_silence_"]
    stop = [r for r in said if r["label"] == "stop"]
    held = [r for r in other if r["label"] in held_out]
    count = lambda rs, o: sum(r["outcome"] == o for r in rs)  # noqa: E731
    return {
        "said": len(said),
        "right": count(said, "right"),
        "wrong": count(said, "wrong"),
        "missed": count(said, "missed"),
        "other": len(other),
        "false": count(other, "false"),
        "silence": len(silence),
        "silence_false": count(silence, "false"),
        # the unsafe case: a stop not heard, or heard as another word (go above all)
        "stop": len(stop),
        "stop_missed": count(stop, "missed"),
        "stop_as_go": sum(r["heard"] == "go" for r in stop),
        "stop_as_other": sum(r["outcome"] == "wrong" and r["heard"] != "go" for r in stop),
        "go_as_stop": sum(r["label"] == "go" and r["heard"] == "stop" for r in said),
        "held": len(held),
        "held_false": count(held, "false"),
    }


def vosk_listener(model_dir: Path):
    """The demo's recogniser: a clip in, the command words Vosk returns out."""
    import vosk

    vosk.SetLogLevel(-1)
    model = vosk.Model(str(model_dir))

    def listen(clip: np.ndarray) -> list[tuple[str, float]]:
        rec = vosk.KaldiRecognizer(model, TARGET_RATE, grammar())
        rec.SetWords(True)
        rec.AcceptWaveform(np.concatenate([clip, SILENCE]).tobytes())
        return parse_result(rec.FinalResult())

    return listen


def kws_listener(checkpoint: Path, threshold: float, stop_any: bool = False):
    """A trained BC-ResNet (demo/kws.py): its top class, if that is a command word at least as
    likely as threshold. With stop_any, "stop" acts at any probability when it is the top class,
    as the core obeys a spoken stop at any trust."""
    import torch

    from demo import kws

    saved = torch.load(checkpoint, map_location="cpu")
    model = kws.BCResNet(saved["tau"])
    model.load_state_dict(saved["state"])
    model.eval()
    mel = kws.mel()

    def listen(clip: np.ndarray) -> list[tuple[str, float]]:
        wave = np.pad(clip, (0, max(0, TARGET_RATE - clip.size)))[:TARGET_RATE]
        x = mel(torch.from_numpy(wave.astype(np.float32) / 32768)[None]).unsqueeze(1)
        with torch.no_grad():
            p = torch.softmax(model(x), 1)[0]
        i = int(p.argmax())
        word, prob = kws.LABELS[i], float(p[i])
        least = 0.0 if stop_any and word == "stop" else threshold
        return [(word, prob)] if word in COMMAND_WORDS and prob >= least else []

    return listen


def rethreshold(rows: list[dict], threshold: float, stop_any: bool = False) -> list[dict]:
    """The rows of a run at threshold 0 (each clip's top command word and its probability), as a
    run at this threshold would give them, so a threshold is chosen without running the model
    again."""
    out = []
    for r in rows:
        least = 0.0 if stop_any and r["heard"] == "stop" else threshold
        heard = r["heard"] if r["heard"] and float(r["conf"] or 0) >= least else ""
        out.append(
            {**r, "heard": heard, "outcome": outcome(r["label"], [(heard, 1.0)] if heard else [])}
        )
    return out


def choose_threshold(rows: list[dict], keep: float = 0.9, step: float = 0.05) -> float:
    """The highest threshold, in steps of step, that keeps keep of the command clips right: the
    rule used on the validation list's clean run at threshold 0 (val_kws_0_clean.csv)."""
    best = 0.0
    for k in range(round(1 / step)):
        t = round(k * step, 4)  # 17 * 0.05 is 0.8500000000000001, above a clip's 0.85
        s = summary(rethreshold(rows, t))
        if s["said"] and s["right"] / s["said"] >= keep:
            best = t
    return best


def choose_report(paths: list[Path], keep: float = 0.9) -> int:
    """--choose: the threshold from the first CSV (the clean validation run at threshold 0), and
    what stop at any changes there in each CSV given."""
    runs = [(path, list(csv.DictReader(open(path, encoding="utf-8")))) for path in paths]
    t = choose_threshold(runs[0][1], keep)
    s = summary(rethreshold(runs[0][1], t))
    print(f"threshold {t:g}: {s['right']} of {s['said']} command clips right "
          f"({s['right'] / s['said']:.1%}) in {paths[0].name}")  # fmt: skip
    for path, rows in runs:
        for stop_any in (False, True):
            kept = rethreshold(rows, t, stop_any)
            s = summary(kept)
            other = [
                r for r in kept if r["label"] not in COMMAND_WORDS and r["label"] != "_silence_"
            ]
            as_stop = sum(r["heard"] == "stop" for r in other)
            print(f"{path.name}, stop {'at any' if stop_any else f'at {t:g}'}: "
                  f"{s['stop_missed']} of {s['stop']} stops missed, {s['stop_as_go']} heard as go; "
                  f"{as_stop} of {s['other']} other words heard as stop")  # fmt: skip
    return 0


def run(
    sc: Path,
    listen,
    snr: float | None,
    seed: int = 0,
    listed: str = "testing_list.txt",
    official: Path | None = None,
) -> list[dict]:
    rng = random.Random(seed)  # the same noise for every recogniser
    noises = [
        noise_part(read_wav(p), "test") for p in sorted((sc / "_background_noise_").glob("*.wav"))
    ]
    clips = official_clips(official) if official else test_clips(sc, listed)
    root = official or sc
    rows = []
    for i, (label, path) in enumerate(clips):
        clip = read_wav(path)
        if snr is not None:
            noise = rng.choice(noises)
            start = rng.randrange(0, noise.size - clip.size)
            clip = mix(clip, noise[start:], snr)
        t = time.perf_counter()
        heard = listen(clip)
        ms = (time.perf_counter() - t) * 1000
        rows.append({
            "clip": path.relative_to(root).as_posix(),
            "label": label,
            "heard": heard[0][0] if heard else "",
            "conf": round(heard[0][1], 3) if heard else "",
            "outcome": outcome(label, heard),
            "ms": round(ms, 1),
        })  # fmt: skip
        if (i + 1) % 1000 == 0:
            print(f"{'clean' if snr is None else f'{snr:g} dB'}: {i + 1} clips", flush=True)
    return rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--sc", type=Path, help="Speech Commands v2, unpacked")
    which = p.add_mutually_exclusive_group()
    which.add_argument("--vosk", type=Path, help="a Vosk model folder")
    which.add_argument("--kws", type=Path, help="a BC-ResNet checkpoint (tools/train_commands.py)")
    p.add_argument("--threshold", type=float, default=0.0,
                   help="BC-ResNet: the least probability of a command word to act on")  # fmt: skip
    p.add_argument("--stop-any", action="store_true",
                   help="BC-ResNet: stop at any probability, others at --threshold")  # fmt: skip
    p.add_argument("--choose", type=Path, nargs="+", metavar="CSV",
                   help="choose the threshold from a validation run at threshold 0 "
                        "(val_kws_0_clean.csv first), without the model")  # fmt: skip
    p.add_argument("--snr", type=float, nargs="*", default=[20.0, 10.0, 5.0, 0.0],
                   help="noise levels to test, besides clean (dB)")  # fmt: skip
    p.add_argument("--out", type=Path, default=None, help="where the per-clip CSVs go")
    p.add_argument("--list", default="testing_list.txt",
                   help="testing_list.txt, or validation_list.txt for a threshold")  # fmt: skip
    p.add_argument("--official", type=Path,
                   help="the authors' test set, speech_commands_test_set_v0.02")  # fmt: skip
    p.add_argument("--held-out", nargs="*", default=[],
                   help="words left out of training (train_commands.py --hold-out)")  # fmt: skip
    a = p.parse_args(argv)
    if a.choose:
        return choose_report(a.choose)
    if not a.sc or not (a.vosk or a.kws):
        p.error("--sc and one of --vosk or --kws are needed")
    out = a.out or a.sc
    if a.vosk:
        listen, prefix = vosk_listener(a.vosk), "vosk"
    else:
        listen = kws_listener(a.kws, a.threshold, a.stop_any)
        prefix = f"kws_{a.threshold:g}{'s' if a.stop_any else ''}"
    held = frozenset(a.held_out)
    part = "official_" if a.official else "" if a.list == "testing_list.txt" else "val_"
    pct = lambda n, d: f"{n} ({n / d:.1%})" if d else "-"  # noqa: E731
    results = []
    print("| Sound | Command clips | Right | Wrong | Missed | Other words | False activations |")
    print("|---|---|---|---|---|---|---|")
    for snr in [None, *a.snr]:
        name = "clean" if snr is None else f"snr{snr:g}"
        sound = "clean" if snr is None else f"noise at {snr:g} dB"
        rows = run(a.sc, listen, snr, listed=a.list, official=a.official)
        with open(out / f"{part}{prefix}_{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        s = summary(rows, held)
        results.append((sound, s))
        print(
            f"| {sound} | {s['said']} "
            f"| {pct(s['right'], s['said'])} | {pct(s['wrong'], s['said'])} "
            f"| {pct(s['missed'], s['said'])} | {s['other']} | {pct(s['false'], s['other'])} |",
            flush=True,
        )
    print(
        "\n| Sound | Stop clips | Missed | Heard as go | Heard as another word | Go heard as stop |"
    )
    print("|---|---|---|---|---|---|")
    for sound, s in results:
        print(f"| {sound} | {s['stop']} | {pct(s['stop_missed'], s['stop'])} "
              f"| {pct(s['stop_as_go'], s['stop'])} | {pct(s['stop_as_other'], s['stop'])} "
              f"| {s['go_as_stop']} |")  # fmt: skip
    if any(s["silence"] for _, s in results):
        said = [f"{sound} {pct(s['silence_false'], s['silence'])}" for sound, s in results]
        print("\nNoise alone (_silence_ clips): " + "; ".join(said))
    if held:
        said = [f"{sound} {pct(s['held_false'], s['held'])}" for sound, s in results]
        print("\nWords left out of training: " + "; ".join(said))
    return 0


if __name__ == "__main__":
    sys.exit(main())
