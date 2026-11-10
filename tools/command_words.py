"""Offline command words on Speech Commands v2 (#46): the demo's Vosk recogniser, or BC-ResNet.

    python tools/command_words.py --sc datasets/speech_commands --vosk vosk-model-small-en-us-0.15
    python tools/command_words.py --sc datasets/speech_commands --kws kws8/model.pt --threshold 0.9

The recogniser is the demo's own (demo/speech.py): Vosk limited to the ten command words, with
"[unk]" for anything else. Each clip of the test list (testing_list.txt) is given as the demo gives
the phone's sound, 16 kHz 16-bit, followed by 0.3 s of silence, and the first command word it
returns is the answer.
- A clip of one of the ten words is right, wrong (another command word) or missed (none).
- A clip of any other word (bed, bird, forward, ...) that returns a command word is a false
  activation: the chair would act on something the user did not say to it.

Each condition is run clean and with Speech Commands' own background noise (_background_noise_:
white and pink noise, a running tap, an exercise bike, dishes, a cat) mixed in at a given
signal-to-noise ratio, a random stretch of a random noise file per clip, with a fixed seed.
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


def summary(rows: list[dict]) -> dict:
    said = [r for r in rows if r["label"] in COMMAND_WORDS]
    other = [r for r in rows if r["label"] not in COMMAND_WORDS]
    count = lambda rs, o: sum(r["outcome"] == o for r in rs)  # noqa: E731
    return {
        "said": len(said),
        "right": count(said, "right"),
        "wrong": count(said, "wrong"),
        "missed": count(said, "missed"),
        "other": len(other),
        "false": count(other, "false"),
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


def kws_listener(checkpoint: Path, threshold: float):
    """A trained BC-ResNet (demo/kws.py): its top class, if that is a command word at least as
    likely as threshold."""
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
        return [(word, prob)] if word in COMMAND_WORDS and prob >= threshold else []

    return listen


def run(
    sc: Path, listen, snr: float | None, seed: int = 0, listed: str = "testing_list.txt"
) -> list[dict]:
    rng = random.Random(seed)  # the same noise for every recogniser
    noises = [read_wav(p) for p in sorted((sc / "_background_noise_").glob("*.wav"))]
    rows = []
    for i, (label, path) in enumerate(test_clips(sc, listed)):
        clip = read_wav(path)
        if snr is not None:
            noise = rng.choice(noises)
            start = rng.randrange(0, noise.size - clip.size)
            clip = mix(clip, noise[start:], snr)
        t = time.perf_counter()
        heard = listen(clip)
        ms = (time.perf_counter() - t) * 1000
        rows.append({
            "clip": path.relative_to(sc).as_posix(),
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
    p.add_argument("--sc", type=Path, required=True, help="Speech Commands v2, unpacked")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--vosk", type=Path, help="a Vosk model folder")
    which.add_argument("--kws", type=Path, help="a BC-ResNet checkpoint (tools/train_commands.py)")
    p.add_argument("--threshold", type=float, default=0.0,
                   help="BC-ResNet: the least probability of a command word to act on")  # fmt: skip
    p.add_argument("--snr", type=float, nargs="*", default=[20.0, 10.0, 5.0, 0.0],
                   help="noise levels to test, besides clean (dB)")  # fmt: skip
    p.add_argument("--out", type=Path, default=None, help="where the per-clip CSVs go")
    p.add_argument("--list", default="testing_list.txt",
                   help="testing_list.txt, or validation_list.txt for a threshold")  # fmt: skip
    a = p.parse_args(argv)
    out = a.out or a.sc
    if a.vosk:
        listen, prefix = vosk_listener(a.vosk), "vosk"
    else:
        listen, prefix = kws_listener(a.kws, a.threshold), f"kws_{a.threshold:g}"
    print("| Sound | Command clips | Right | Wrong | Missed | Other words | False activations |")
    print("|---|---|---|---|---|---|---|")
    for snr in [None, *a.snr]:
        name = "clean" if snr is None else f"snr{snr:g}"
        rows = run(a.sc, listen, snr, listed=a.list)
        part = "" if a.list == "testing_list.txt" else "val_"
        with open(out / f"{part}{prefix}_{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        s = summary(rows)
        pct = lambda n, d: f"{n} ({n / d:.1%})"  # noqa: E731
        print(
            f"| {'clean' if snr is None else f'noise at {snr:g} dB'} | {s['said']} "
            f"| {pct(s['right'], s['said'])} | {pct(s['wrong'], s['said'])} "
            f"| {pct(s['missed'], s['said'])} | {s['other']} | {pct(s['false'], s['other'])} |",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
