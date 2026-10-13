"""Voice commands for the phone demo: a lightweight offline speech recogniser limited to the
command words, and the audio helpers it needs. The model is optional: if Vosk or its model is not
installed, `CommandRecognizer.load` returns None and the voice tile says so.

The browser's own speech recognition ("stop" and "go") is a separate, online path in the phone
page and goes through the same command mapping in `middleware.WORD_COMMANDS`.
"""

from __future__ import annotations

import importlib
import json
import struct
from pathlib import Path

# The ten words of the Speech Commands set that the design uses as chair commands.
COMMAND_WORDS = ("stop", "go", "yes", "no", "left", "right", "up", "down", "on", "off")
TARGET_RATE = 16000


def grammar() -> str:
    """The word list Vosk is limited to, as JSON. "[unk]" absorbs everything else."""
    return json.dumps([*COMMAND_WORDS, "[unk]"])


def parse_result(text: str) -> list[tuple[str, float]]:
    """(word, confidence) pairs from a Vosk result, command words only."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    found = []
    for item in data.get("result", []) or []:
        word = str(item.get("word", "")).lower()
        if word in COMMAND_WORDS:
            found.append((word, float(item.get("conf", 0.0))))
    if not found:  # a result without per-word details
        for word in str(data.get("text", "")).lower().split():
            if word in COMMAND_WORDS:
                found.append((word, 0.5))
    return found


def to_pcm16_16k(samples, source_rate: int) -> bytes:
    """Mono float samples (-1 to 1) at source_rate as 16 kHz 16-bit little-endian PCM.

    It averages the source samples that fall into each output sample, which is enough for
    recognising short command words.
    """
    n = len(samples)
    if n == 0 or source_rate <= 0:
        return b""
    out_len = int(n * TARGET_RATE / source_rate)
    ratio = source_rate / TARGET_RATE
    out = []
    for i in range(out_len):
        lo = int(i * ratio)
        hi = max(lo + 1, int((i + 1) * ratio))
        chunk = samples[lo:hi]
        v = sum(chunk) / len(chunk)
        out.append(max(-32768, min(32767, int(v * 32767))))
    return struct.pack(f"<{len(out)}h", *out)


class CommandRecognizer:
    """Vosk, limited to the command words."""

    def __init__(self, recognizer):
        self.recognizer = recognizer

    @classmethod
    def load(cls, model_dir: str) -> CommandRecognizer | None:
        if not Path(model_dir).is_dir():
            return None
        try:
            vosk = importlib.import_module("vosk")
            vosk.SetLogLevel(-1)
            rec = vosk.KaldiRecognizer(vosk.Model(model_dir), TARGET_RATE, grammar())
            rec.SetWords(True)
            return cls(rec)
        except Exception:  # vosk is not installed, or the model cannot be loaded
            return None

    def feed(self, pcm16_16k: bytes) -> list[tuple[str, float]]:
        """Audio in, any finished command words out."""
        if self.recognizer.AcceptWaveform(pcm16_16k):
            return parse_result(self.recognizer.Result())
        return []
