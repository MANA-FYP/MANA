"""People from Wi-Fi: an ESP32's channel state information (CSI) turned into a presence report.

How it works. A Wi-Fi signal reaches the receiver along many paths at once: straight, and off the
walls, the floor and anyone in the room. A person who moves changes those paths, so the strength
of each of the 56 subcarriers of a 20 MHz channel flickers, each in its own way. An ESP32 reports
that per-subcarrier amplitude for every packet it receives (CSI); a laptop, a phone browser or a
Windows program gets only one number, the RSSI, refreshed rarely (`demo/wifi.py`). So this needs
an ESP32 as the receiver (with esp-csi's console firmware it prints one ``CSI_DATA`` line per
packet on its USB serial port) and a second ESP32 or any access point sending packets.

What is computed, per one-second window of packets:
- **motion**: per subcarrier, how much its amplitude varies (standard deviation over mean), then
  the median over subcarriers. A still, empty room keeps it low; a person moving raises it.
- **features** for the zone model: that variation per subcarrier, and the shape of the mean
  amplitude across subcarriers (each divided by their sum, so a gain change does not move it).
  The shape changes with where a body stands in the paths, even when it is still.

The presence threshold and the zone model are fitted on recordings of the room (empty, and a
person in each zone) and do not carry over to another room or another placement of the two
ESP32s: each place needs its own short calibration (`tools/wifi_csi.py`).
"""

from __future__ import annotations

import csv
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import numpy as np

# 64 subcarriers in esp-csi's order (-32 .. 31); the guard bands and the DC one carry nothing
SUBCARRIERS = np.r_[4:32, 33:61]
# where rssi (dBm), local_timestamp (us) and data are in a CSI_DATA row of the Office Localization
# recordings. Other esp-csi firmware prints other rows, so a "type,..." header line, when one comes,
# names the columns instead (LineParser)
DATASET_COLUMNS = {"rssi": 3, "local_timestamp": 9, "data": 14}


@dataclass
class Packet:
    t: float  # seconds, the ESP32's clock
    rssi: int
    amp: np.ndarray  # (56,) amplitude per subcarrier


def columns(row: list[str]) -> dict[str, int] | None:
    """The column of each field read, from a header row ("type,seq,mac,rssi,..."); None when it
    is not one or lacks a field."""
    names = [c.strip() for c in row]
    if not names or names[0] != "type" or not all(n in names for n in DATASET_COLUMNS):
        return None
    return {n: names.index(n) for n in DATASET_COLUMNS}


def parse_row(row: list[str], cols: dict[str, int] = DATASET_COLUMNS) -> Packet | None:
    """One CSI_DATA row (already split into fields) to a Packet; None for anything else."""
    if len(row) <= max(cols.values()) or row[0] != "CSI_DATA":
        return None
    try:
        v = np.array(row[cols["data"]].strip(' "[]').split(","), dtype=np.float32)
        t, rssi = int(row[cols["local_timestamp"]]) / 1e6, int(row[cols["rssi"]])
    except ValueError:
        return None
    if v.size != 128:
        return None
    iq = v.reshape(64, 2)
    return Packet(t, rssi, np.hypot(iq[SUBCARRIERS, 0], iq[SUBCARRIERS, 1]))


def parse_line(line: str, cols: dict[str, int] = DATASET_COLUMNS) -> Packet | None:
    """A line from the ESP32's serial port or a recording. A row is always one line, so a quote
    left open (a cut line) spoils only that line."""
    if not line.startswith("CSI_DATA"):
        return None
    try:
        return parse_row(next(csv.reader([line])), cols)
    except csv.Error:  # a field past the csv module's limit
        return None


class LineParser:
    """Lines to packets, with the columns from the last "type,..." header seen (the dataset's
    layout until one comes). It counts the CSI_DATA lines and the packets read from them, so a
    board whose rows it cannot read is said, not shown as zero packets (Komail, #195)."""

    def __init__(self, warn_after_s: float = 5.0):
        self.cols, self.warn_after_s = dict(DATASET_COLUMNS), warn_after_s
        self.lines = self.packets = 0
        self._first: float | None = None
        self._warned = False

    def __call__(self, line: str) -> Packet | None:
        if line.startswith("type,"):
            try:
                cols = columns(next(csv.reader([line])))
            except csv.Error:
                cols = None
            if cols is not None:
                self.cols = cols
            return None
        if not line.startswith("CSI_DATA"):
            return None
        self.lines += 1
        p = parse_line(line, self.cols)
        self.packets += p is not None
        return p

    def unread(self, now: float) -> str | None:
        """Once, when CSI_DATA lines have come for warn_after_s and none was read: why."""
        if self.lines and self._first is None:
            self._first = now
        if self._warned or self.packets or self._first is None:
            return None
        if now - self._first < self.warn_after_s:
            return None
        self._warned = True
        return (
            f"{self.lines} CSI_DATA lines, none read: the columns rssi, local_timestamp and data "
            f"are taken from a 'type,...' header line (now {self.cols}), and data must hold 128 "
            "values (64 subcarriers, I and Q)"
        )


def read_file(path) -> Iterator[Packet]:
    parse = LineParser()
    with open(path, newline="") as f:
        for line in f:
            p = parse(line.rstrip("\r\n"))
            if p is not None:
                yield p


def motion(amps: np.ndarray) -> float:
    """Median over subcarriers of the amplitude's standard deviation over its mean."""
    m = amps.mean(0)
    return float(np.median(amps.std(0) / (m + 1e-9)))


def features(amps: np.ndarray) -> np.ndarray:
    m = amps.mean(0)
    return np.r_[amps.std(0) / (m + 1e-9), m / (m.sum() + 1e-9)]


def windows(packets: Iterable[Packet], size_s: float = 1.0, min_packets: int = 20):
    """(start time, amplitudes) per window of size_s seconds; sparse windows are skipped."""
    start, buf = None, []
    for p in packets:
        if start is None or p.t < start:  # the first packet, or the ESP32 restarted
            start, buf = p.t, []
        if p.t - start >= size_s:
            if len(buf) >= min_packets:
                yield start, np.array(buf)
            start, buf = p.t, []
        buf.append(p.amp)
    if start is not None and len(buf) >= min_packets:
        yield start, np.array(buf)


@dataclass
class ZoneModel:
    """A linear model (multinomial logistic regression) on standardised features: fitted once by
    tools/wifi_csi.py with scikit-learn, then run here with numpy alone, from a small .npz."""

    labels: list[str]
    mean: np.ndarray
    scale: np.ndarray
    coef: np.ndarray  # (labels, features)
    intercept: np.ndarray  # (labels,)
    threshold: float  # motion over this is movement (the 99th percentile in the empty room)

    def scores(self, f: np.ndarray) -> np.ndarray:
        """The model's probability for each label."""
        z = self.coef @ ((f - self.mean) / self.scale) + self.intercept
        e = np.exp(z - z.max())
        return e / e.sum()

    def predict(self, f: np.ndarray) -> str:
        return self.labels[int(np.argmax(self.scores(f)))]

    def save(self, path) -> None:
        np.savez(
            path,
            labels=np.array(self.labels),
            mean=self.mean,
            scale=self.scale,
            coef=self.coef,
            intercept=self.intercept,
            threshold=self.threshold,
        )

    @classmethod
    def load(cls, path) -> ZoneModel:
        z = np.load(path, allow_pickle=False)
        return cls(
            [str(s) for s in z["labels"]],
            z["mean"],
            z["scale"],
            z["coef"],
            z["intercept"],
            float(z["threshold"]),
        )


@dataclass
class Sensing:
    """Live state for the screen: the last packets, the last windows and their verdicts."""

    model: ZoneModel | None = None
    window_s: float = 1.0
    source: str = "none"  # "replay <file>" or "serial <port>"
    packets: int = 0
    _buf: list = field(default_factory=list)
    _start: float | None = None
    rows: deque = field(default_factory=lambda: deque(maxlen=100))  # (t, amp) at 10 per second
    history: deque = field(default_factory=lambda: deque(maxlen=60))  # one per window
    _last_row_t: float = -1.0
    _row: list = field(default_factory=list)

    def restart(self, source: str) -> None:
        """A new recording or device: the open window starts again. The waterfall keeps its
        rows, so the change from one to the next shows."""
        self.source, self._start, self._buf = source, None, []
        self._last_row_t, self._row = -1.0, []

    def feed(self, p: Packet) -> dict | None:
        """Add one packet; returns the verdict when a window closes."""
        self.packets += 1
        # the waterfall: the mean of each tenth of a second, so one odd packet is not a stripe
        if p.t < self._last_row_t:
            self._last_row_t, self._row = p.t, []
        self._row.append(p.amp)
        if p.t - self._last_row_t >= 0.1:
            self.rows.append((p.t, np.mean(self._row, axis=0)))
            self._last_row_t, self._row = p.t, []
        if self._start is None or p.t < self._start:
            self._start, self._buf = p.t, []
        out = None
        if p.t - self._start >= self.window_s:
            if len(self._buf) >= 20:
                out = self.judge(self._start, np.array(self._buf), len(self._buf) / self.window_s)
            self._start, self._buf = p.t, []
        self._buf.append(p.amp)
        return out

    def judge(self, t: float, amps: np.ndarray, rate: float) -> dict:
        mo = motion(amps)
        v = {"t": round(t, 2), "motion": round(mo, 4), "rate_hz": round(rate)}
        if self.model is not None:
            s = self.model.scores(features(amps))
            label = self.model.labels[int(np.argmax(s))]
            v.update(
                moving=bool(mo > self.model.threshold),
                label=label,
                present=label != "empty",
                scores={k: round(float(x), 3) for k, x in zip(self.model.labels, s, strict=True)},
            )
        self.history.append(v)
        return v

    def view(self) -> dict:
        rows = list(self.rows)
        # stretched between the 2nd and 98th percentile of what is shown, so the pattern shows
        lo, hi = np.percentile(np.array([r[1] for r in rows]), [2, 98]) if rows else (0.0, 1.0)
        span = float(hi - lo) or 1.0
        return {
            "source": self.source,
            "packets": self.packets,
            "threshold": None if self.model is None else round(self.model.threshold, 4),
            "labels": None if self.model is None else self.model.labels,
            # amplitudes scaled to 0-255 for the waterfall: oldest first, 56 per row
            "waterfall": [
                np.clip((r[1] - lo) / span * 255, 0, 255).astype(int).tolist() for r in rows
            ],
            "history": list(self.history),
            "last": self.history[-1] if self.history else None,
        }


class Replay:
    """Plays recordings at their own pace, one after the other (a tour), or one chosen label.

    recordings: {label: path}. Each file keeps its place between turns, so a long tour shows new
    packets, and starts again at its end."""

    def __init__(self, recordings: dict, sensing: Sensing, turn_s: float = 20.0):
        self.recordings, self.sensing, self.turn_s = dict(recordings), sensing, turn_s
        self.order = list(self.recordings)
        self.fixed: str | None = None  # a label chosen on the screen; None: the tour
        self.label = self.order[0]
        self._files: dict = {}
        self._turn_start = None
        self.truth_hits = self.truth_total = 0

    def _next_packet(self, label: str) -> Packet | None:
        it = self._files.get(label)
        for _ in range(2):  # at the end of a file, once from the start
            if it is None:
                it = self._files[label] = read_file(self.recordings[label])
            p = next(it, None)
            if p is not None:
                return p
            it = self._files[label] = None
        return None

    def choose(self, label: str | None) -> None:
        if label is not None and label not in self.recordings:
            raise KeyError(label)
        self.fixed = label
        self.label = label or self.label
        self._turn_start = None  # the next step starts the turn

    def _switch(self, label: str, now: float) -> None:
        self.label, self._turn_start = label, now
        self.sensing.restart(f"replay: {label}")
        self._clock = None  # (wall time, packet time) at the start of the turn
        self._peek = None

    def step(self, now: float, max_packets: int = 400) -> list[dict]:
        """Feed every packet due by the wall clock `now`; returns the verdicts of closed windows."""
        if self._turn_start is None:
            self._switch(self.fixed or self.label, now)
        elif self.fixed is None and now - self._turn_start >= self.turn_s:
            i = (self.order.index(self.label) + 1) % len(self.order)
            self._switch(self.order[i], now)
        out = []
        for _ in range(max_packets):
            p = self._peek or self._next_packet(self.label)
            self._peek = None
            if p is None:
                break
            if self._clock is None or p.t < self._clock[1]:
                self._clock = (now, p.t)
            if p.t - self._clock[1] > now - self._clock[0]:  # not yet due
                self._peek = p
                break
            v = self.sensing.feed(p)
            if v is not None:
                v["truth"] = self.label
                if "label" in v:
                    self.truth_total += 1
                    self.truth_hits += v["label"] == self.label
                out.append(v)
        return out

    _peek: Packet | None = None
    _clock: tuple | None = None

    def view(self) -> dict:
        return {
            "labels": self.order,
            "playing": self.label,
            "fixed": self.fixed,
            "turn_s": self.turn_s,
            "right": self.truth_hits,
            "judged": self.truth_total,
        }


def serial_lines(port: str, baud: int, stop=None):
    """Lines from an ESP32 on a serial port (pyserial) until `stop` (a threading.Event) is set;
    blocking, so run in a thread. A read waits at most 1 s, so a quiet board still lets it stop."""
    import serial  # pyserial, only when an ESP32 is used

    with serial.Serial(port, baud, timeout=1) as s:
        while stop is None or not stop.is_set():
            line = s.readline().decode("ascii", "replace").strip()
            if line:
                yield line
