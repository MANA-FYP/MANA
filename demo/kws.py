"""BC-ResNet keyword spotting for the ten command words, offline (#46).

The model is BC-ResNet (Kim, Chang, Lee and Sung, "Broadcasted Residual Learning for Efficient
Keyword Spotting", Interspeech 2021), written here from the paper. Input: 1 s of sound at 16 kHz
as a 40-band log-mel spectrogram (30 ms windows every 10 ms, 101 frames). Output: the ten command
words of demo/speech.py, "unknown" (any other word) and "silence" (no word), the 12 classes of the
Speech Commands benchmark the paper reports on.

Each block filters along frequency with a depthwise convolution and sub-spectral normalisation,
averages over frequency, filters that along time (depthwise, dilated), and broadcasts it back over
frequency, beside the identity. BC-ResNet-tau scales the channels by tau: the paper's BC-ResNet-8
has about 321k parameters. Needs torch and torchaudio.
"""

from __future__ import annotations

import numpy as np
import torch
from torch import nn

from demo.speech import COMMAND_WORDS

LABELS = (*COMMAND_WORDS, "unknown", "silence")
RATE = 16000
N_MELS = 40


class SubSpectralNorm(nn.Module):
    """Batch norm per band of frequency: the frequency axis cut into `bands` sub-bands."""

    def __init__(self, channels: int, bands: int = 5):
        super().__init__()
        self.bands = bands
        self.bn = nn.BatchNorm2d(channels * bands)

    def forward(self, x):
        n, c, f, t = x.shape
        x = self.bn(x.reshape(n, c * self.bands, f // self.bands, t))
        return x.reshape(n, c, f, t)


class BCBlock(nn.Module):
    """A broadcasted residual block. A transition block changes the channel count (and may
    stride along frequency) and has no identity shortcut."""

    def __init__(self, cin: int, cout: int, stride: int = 1, dilation: int = 1, drop: float = 0.1):
        super().__init__()
        self.transition = cin != cout
        self.pre = (
            nn.Sequential(nn.Conv2d(cin, cout, 1, bias=False), nn.BatchNorm2d(cout), nn.ReLU())
            if self.transition
            else nn.Identity()
        )
        self.f2 = nn.Sequential(  # along frequency, per channel
            nn.Conv2d(cout, cout, (3, 1), stride=(stride, 1), padding=(1, 0), groups=cout,
                      bias=False),
            SubSpectralNorm(cout),
        )  # fmt: skip
        self.f1 = nn.Sequential(  # along time, on the frequency average
            nn.Conv2d(cout, cout, (1, 3), padding=(0, dilation), dilation=(1, dilation),
                      groups=cout, bias=False),
            nn.BatchNorm2d(cout),
            nn.SiLU(),
            nn.Conv2d(cout, cout, 1, bias=False),
            nn.Dropout2d(drop),
        )  # fmt: skip
        self.act = nn.ReLU()

    def forward(self, x):
        x = self.pre(x)
        y = self.f2(x)
        z = self.f1(y.mean(dim=2, keepdim=True))  # broadcast back over frequency
        out = y + z if self.transition else x + y + z
        return self.act(out)


class BCResNet(nn.Module):
    def __init__(self, tau: float = 8, classes: int = len(LABELS)):
        super().__init__()
        c = [int(round(tau * k)) for k in (16, 8, 12, 16, 20, 32)]
        self.head = nn.Sequential(
            nn.Conv2d(1, c[0], 5, stride=(2, 1), padding=2, bias=False),
            nn.BatchNorm2d(c[0]),
            nn.ReLU(),
        )
        blocks, cin = [], c[0]
        # (channels, blocks, frequency stride of the first, time dilation): the paper's stages
        for cout, n, stride, dilation in ((c[1], 2, 1, 1), (c[2], 2, 2, 2), (c[3], 4, 2, 4),
                                          (c[4], 4, 1, 8)):  # fmt: skip
            for i in range(n):
                blocks.append(BCBlock(cin, cout, stride if i == 0 else 1, dilation))
                cin = cout
        self.blocks = nn.Sequential(*blocks)
        self.tail = nn.Sequential(
            nn.Conv2d(cin, cin, 5, groups=cin, padding=(0, 2), bias=False),  # no pad on frequency
            nn.Conv2d(cin, c[5], 1, bias=False),
            nn.BatchNorm2d(c[5]),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(c[5], classes, 1),
        )

    def forward(self, x):  # (batch, 1, N_MELS, frames)
        return self.tail(self.blocks(self.head(x))).flatten(1)


def mel():
    """The log-mel front end: 30 ms windows every 10 ms, 40 bands."""
    import torchaudio

    spec = torchaudio.transforms.MelSpectrogram(
        sample_rate=RATE, n_fft=480, win_length=480, hop_length=160, n_mels=N_MELS
    )
    return lambda wave: torch.log(spec(wave) + 1e-6)


class Spotter:
    """The command words in a stream of sound: the last 1 s is scored every hop_s, and a word
    that passes `threshold` is given once, then not again until rearm_s has gone by (a word lasts
    up to a second, so it is in several windows). Same use as speech.CommandRecognizer: feed()
    takes 16 kHz 16-bit PCM and gives (word, confidence) pairs."""

    def __init__(self, score, threshold: float = 0.9, hop_s: float = 0.25, rearm_s: float = 1.0):
        self.score = score  # 1 s of float sound -> a probability per class of LABELS
        self.threshold, self.hop, self.rearm = threshold, int(hop_s * RATE), int(rearm_s * RATE)
        self.sound = np.zeros(0, dtype=np.float32)
        self.since_scored = 0  # samples heard since the last score
        self.since_said = self.rearm  # samples since a word was last given

    def feed(self, pcm16_16k: bytes) -> list[tuple[str, float]]:
        new = np.frombuffer(pcm16_16k, dtype="<i2").astype(np.float32) / 32768
        self.sound = np.concatenate([self.sound, new])[-RATE:]
        self.since_scored += new.size
        self.since_said += new.size
        out = []
        if self.since_scored >= self.hop and self.sound.size == RATE:  # once: the newest second
            self.since_scored = 0
            p = self.score(self.sound)
            i = int(np.argmax(p))
            word = LABELS[i]
            if word in COMMAND_WORDS and p[i] >= self.threshold and self.since_said >= self.rearm:
                out.append((word, float(p[i])))
                self.since_said = 0
        return out

    @classmethod
    def load(cls, checkpoint: str, threshold: float = 0.9) -> Spotter | None:
        """From a checkpoint of tools/train_commands.py; None when it cannot be loaded."""
        try:
            saved = torch.load(checkpoint, map_location="cpu")
            model = BCResNet(saved["tau"])
            model.load_state_dict(saved["state"])
        except (OSError, KeyError, RuntimeError):
            return None
        model.eval()
        features = mel()

        def score(wave: np.ndarray) -> np.ndarray:
            with torch.no_grad():
                x = features(torch.from_numpy(wave)[None]).unsqueeze(1)
                return torch.softmax(model(x), 1)[0].numpy()

        return cls(score, threshold)
