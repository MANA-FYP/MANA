"""Train BC-ResNet (demo/kws.py) on Speech Commands v2 for the ten command words (#46).

    python tools/train_commands.py --sc datasets/speech_commands --tau 8 --epochs 12 --out kws8

The split is the dataset's own: validation_list.txt and testing_list.txt, the rest for training.
12 classes: the ten command words, "unknown" (the other 25 words) and "silence". Each epoch takes
every command-word clip, and as many unknown clips as an average word has, drawn anew, and as
many silences (a stretch of a background noise file at a random level, or nothing). Augmentation,
as in the paper: a shift of up to 100 ms, background noise mixed in on 80% of the clips at 0 to
20 dB below the speech, and SpecAugment (two frequency and two time masks). SGD with momentum
0.9, weight decay 0.001, learning rate 0.1 with one epoch of warm-up and a cosine decay, batches
of 100. The paper trains for 200 epochs on a GPU; on this laptop's CPU an epoch of BC-ResNet-8
takes about 35 minutes, so fewer are run and the number is reported.

The model with the best validation accuracy is kept as <out>/model.pt, with a log per epoch.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from command_words import read_wav  # noqa: E402

from demo import kws  # noqa: E402

WORDS = kws.LABELS[:10]
UNKNOWN, SILENCE = kws.LABELS.index("unknown"), kws.LABELS.index("silence")


def splits(sc: Path) -> dict[str, list[tuple[int, Path]]]:
    """(class, path) of every clip, by split. Background noise is not a word."""
    val = set((sc / "validation_list.txt").read_text().split())
    test = set((sc / "testing_list.txt").read_text().split())
    out = {"train": [], "val": [], "test": []}
    for wav in sorted(sc.glob("*/*.wav")):
        label = wav.parent.name
        if label.startswith("_"):
            continue
        rel = f"{label}/{wav.name}"
        cls = WORDS.index(label) if label in WORDS else UNKNOWN
        out["val" if rel in val else "test" if rel in test else "train"].append((cls, wav))
    return out


def fixed(clip: np.ndarray, n: int = kws.RATE) -> np.ndarray:
    """A clip as exactly 1 s, padded with silence or cut."""
    return np.pad(clip, (0, max(0, n - clip.size)))[:n]


class Augment:
    def __init__(self, noises: list[np.ndarray], rng: random.Random):
        self.noises, self.rng = [n.astype(np.float32) / 32768 for n in noises], rng

    def noise(self, n: int = kws.RATE) -> np.ndarray:
        x = self.rng.choice(self.noises)
        s = self.rng.randrange(0, x.size - n)
        return x[s : s + n]

    def silence(self) -> np.ndarray:
        return (
            np.zeros(kws.RATE, np.float32)
            if self.rng.random() < 0.1
            else self.noise() * (self.rng.uniform(0.0, 0.5))
        )

    def __call__(self, wave: np.ndarray) -> np.ndarray:
        shift = self.rng.randint(-1600, 1600)
        wave = np.roll(wave, shift)
        if shift > 0:
            wave[:shift] = 0
        elif shift < 0:
            wave[shift:] = 0
        if self.rng.random() < 0.8:
            noise = self.noise()
            ps, pn = np.mean(wave**2), np.mean(noise**2)
            if ps > 0 and pn > 0:
                snr = self.rng.uniform(0, 20)
                wave = wave + noise * np.sqrt(ps / (pn * 10 ** (snr / 10)))
        return wave


def spec_augment(x: torch.Tensor, rng: random.Random) -> torch.Tensor:
    """Two frequency and two time masks per clip, filled with the clip's mean: a log-mel's 0 is
    loud (the mean is about -7), so masking with 0 would add a loud band instead of hiding one."""
    x = x.clone()
    for i in range(x.shape[0]):
        fill = float(x[i].mean())
        for _ in range(2):
            f = rng.randint(0, 7)
            f0 = rng.randint(0, kws.N_MELS - f)
            x[i, :, f0 : f0 + f, :] = fill
            t = rng.randint(0, 20)
            t0 = rng.randint(0, x.shape[-1] - t)
            x[i, :, :, t0 : t0 + t] = fill
    return x


def load(items: list[tuple[int, Path]]) -> tuple[np.ndarray, np.ndarray]:
    """1 s clips as int16 (a third of the memory of float32), and their classes."""
    return np.stack([fixed(read_wav(p)) for _, p in items]), np.array([c for c, _ in items])


def as_float(waves: np.ndarray) -> np.ndarray:
    return waves.astype(np.float32) / 32768 if waves.dtype == np.int16 else waves


def accuracy(model, mel, waves: np.ndarray, labels: np.ndarray, batch: int = 500) -> float:
    model.eval()
    right = 0
    with torch.no_grad():
        for i in range(0, len(labels), batch):
            x = mel(torch.from_numpy(as_float(waves[i : i + batch]))).unsqueeze(1)
            right += int((model(x).argmax(1).numpy() == labels[i : i + batch]).sum())
    return right / len(labels)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--sc", type=Path, required=True)
    p.add_argument("--tau", type=float, default=8)
    p.add_argument("--epochs", type=int, default=12)
    p.add_argument("--batch", type=int, default=100)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    rng, torch_rng = random.Random(a.seed), torch.manual_seed(a.seed)
    del torch_rng
    data = splits(a.sc)
    noises = [read_wav(n) for n in sorted((a.sc / "_background_noise_").glob("*.wav"))]
    aug = Augment(noises, rng)
    words = [x for x in data["train"] if x[0] != UNKNOWN]
    unknown = [x for x in data["train"] if x[0] == UNKNOWN]
    per_class = len(words) // len(WORDS)
    print(f"train: {len(words)} command clips, {len(unknown)} unknown; {per_class} per class",
          flush=True)  # fmt: skip
    t0 = time.time()
    word_waves, word_labels = load(words)
    # validation in the same shares as training: every command word, and as many unknown
    # clips and silences as an average word has (all the unknown clips would be 60% of it)
    val_words = [x for x in data["val"] if x[0] != UNKNOWN]
    val_unknown = random.Random(a.seed).sample(
        [x for x in data["val"] if x[0] == UNKNOWN], len(val_words) // len(WORDS)
    )
    val_waves, val_labels = load(val_words + val_unknown)
    vsil = np.stack([aug.silence() for _ in range(len(val_words) // len(WORDS))])
    val_waves = np.concatenate([as_float(val_waves), vsil])
    val_labels = np.concatenate([val_labels, np.full(len(vsil), SILENCE)])
    print(f"loaded in {time.time() - t0:.0f} s", flush=True)

    mel = kws.mel()
    model = kws.BCResNet(a.tau)
    opt = torch.optim.SGD(model.parameters(), lr=0.1, momentum=0.9, weight_decay=1e-3)
    loss_fn = nn.CrossEntropyLoss()
    steps_per_epoch = math.ceil((len(words) + 2 * per_class) / a.batch)
    total, step, best, log = a.epochs * steps_per_epoch, 0, -1.0, []
    for epoch in range(a.epochs):
        model.train()
        unknown_waves, _ = load(rng.sample(unknown, per_class))  # drawn anew, from disk
        silences = np.stack([aug.silence() for _ in range(per_class)])
        waves = np.concatenate([word_waves, unknown_waves,
                                np.round(silences * 32767).astype(np.int16)])  # fmt: skip
        labels = np.concatenate([word_labels, np.full(per_class, UNKNOWN),
                                 np.full(per_class, SILENCE)])  # fmt: skip
        order = np.random.default_rng(a.seed + epoch).permutation(len(labels))
        t, seen, losses = time.time(), 0, []
        for i in range(0, len(order), a.batch):
            idx = order[i : i + a.batch]
            batch = np.stack([aug(as_float(waves[j])) if labels[j] != SILENCE
                              else as_float(waves[j]) for j in idx]).astype(np.float32)  # fmt: skip
            x = spec_augment(mel(torch.from_numpy(batch)).unsqueeze(1), rng)
            y = torch.from_numpy(labels[idx]).long()
            lr = (
                0.1
                * min(1.0, (step + 1) / steps_per_epoch)
                * 0.5
                * (1 + math.cos(math.pi * step / total))
            )
            for g in opt.param_groups:
                g["lr"] = lr
            loss = loss_fn(model(x), y)
            opt.zero_grad()
            loss.backward()
            opt.step()
            step, seen = step + 1, seen + len(idx)
            losses.append(loss.item())
        val = accuracy(model, mel, val_waves, val_labels)
        minutes = round((time.time() - t) / 60, 1)
        log.append({"epoch": epoch + 1, "loss": round(float(np.mean(losses)), 4),
                    "val_accuracy": round(val, 4), "minutes": minutes})  # fmt: skip
        print(json.dumps(log[-1]), flush=True)
        if val > best:
            best = val
            torch.save({"tau": a.tau, "labels": kws.LABELS, "state": model.state_dict(),
                        "epoch": epoch + 1, "val_accuracy": val}, a.out / "model.pt")  # fmt: skip
        (a.out / "log.json").write_text(json.dumps(log, indent=1))
    print(f"best validation accuracy {best:.4f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
