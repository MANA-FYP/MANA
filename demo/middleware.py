"""Phone middleware for the MANA demo. Pure Python, no network, no ROS.

It sits between the phone and the decision layer:

  phone messages --> sensor registry, report builders, position and link rules --> DecisionCore
                                                                  |
                       a snapshot of everything, for the dashboard and the phone

Every report is stamped with the time the laptop received it, so the phone's clock does not
matter. Nothing here invents data: a sensor the browser cannot read is reported as unavailable,
and the decision layer simply never receives anything from it.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from mana_core.decision import (
    Command,
    DecisionCore,
    Ground,
    GroundReport,
    MotionReport,
    ObstacleReport,
    Source,
)

# ---- sensors the phone page lists


class State(StrEnum):
    ACTIVE = "active"  # streaming now
    IDLE = "idle"  # the browser has it, but nothing is arriving (permission or not started)
    UNAVAILABLE = "unavailable"  # a web page cannot read it


@dataclass(frozen=True)
class SensorInfo:
    id: str
    label: str
    always_unavailable: str | None = None  # the reason, when no browser can provide it


SENSORS = (
    SensorInfo("camera", "Camera"),
    SensorInfo("motion", "Motion"),
    SensorInfo("gps", "GPS"),
    SensorInfo("microphone", "Microphone"),
    SensorInfo("wifi", "Wi-Fi"),
    SensorInfo("bluetooth", "Bluetooth"),
    SensorInfo("depth", "Depth", "Not exposed to browsers"),
    SensorInfo("lidar", "Lidar", "Not exposed to browsers"),
    SensorInfo("temperature", "Temperature", "Not exposed to browsers"),
)
FRESH_S = 2.0  # a sensor counts as active when it reported within this time


@dataclass
class SensorState:
    info: SensorInfo
    browser_has_it: bool = True
    why_not: str = ""
    last_seen: float | None = None
    value: str = ""
    trust: float = 0.0
    stamps: deque = field(default_factory=lambda: deque(maxlen=200))

    def rate_hz(self, now: float, window_s: float = 2.0) -> float:
        return sum(1 for t in self.stamps if now - t <= window_s) / window_s

    def state(self, now: float) -> State:
        if self.info.always_unavailable or not self.browser_has_it:
            return State.UNAVAILABLE
        if self.last_seen is not None and now - self.last_seen <= FRESH_S:
            return State.ACTIVE
        return State.IDLE

    def reason(self) -> str:
        return self.info.always_unavailable or self.why_not


# ---- position source: GPS or the indoor map, with a gap so it does not flip back and forth


class PositionSource:
    """Leave GPS when the accuracy is worse than out_m for out_s seconds; come back when it is
    better than back_m for back_s seconds. Between the two levels nothing changes."""

    GPS, INDOOR = "gps", "indoor"

    def __init__(self, out_m=15.0, out_s=3.0, back_m=8.0, back_s=5.0):
        self.out_m, self.out_s, self.back_m, self.back_s = out_m, out_s, back_m, back_s
        self.source = self.GPS
        self._since: float | None = None

    def update(self, accuracy_m: float | None, now: float) -> str:
        """accuracy_m is None when there is no fix at all."""
        acc = math.inf if accuracy_m is None else accuracy_m
        if self.source == self.GPS:
            if acc > self.out_m:
                self._since = now if self._since is None else self._since
            else:
                self._since = None
            if self._since is not None and now - self._since >= self.out_s:
                self.source, self._since = self.INDOOR, None
        else:
            if acc < self.back_m:
                self._since = now if self._since is None else self._since
            else:
                self._since = None
            if self._since is not None and now - self._since >= self.back_s:
                self.source, self._since = self.GPS, None
        return self.source


# ---- link: Wi-Fi, or the fallback when heartbeats are missed


class LinkSelector:
    """Leave Wi-Fi after missed_to_leave heartbeats in a row; come back after back_s seconds of
    good heartbeats. The page cannot read signal strength, so the heartbeat is the evidence."""

    WIFI, FALLBACK = "wifi", "fallback"

    def __init__(self, period_s=1.0, missed_to_leave=2, back_s=5.0, slow_ms=500.0):
        self.period_s, self.missed_to_leave = period_s, missed_to_leave
        self.back_s, self.slow_ms = back_s, slow_ms
        self.state = self.WIFI
        self.rtt_ms: float | None = None
        self._last_beat: float | None = None
        self._good_since: float | None = None

    def heartbeat(self, now: float, rtt_ms: float | None = None) -> None:
        self._last_beat = now
        if rtt_ms is not None:
            self.rtt_ms = rtt_ms

    def missed(self, now: float) -> int:
        if self._last_beat is None:
            return 0
        return max(0, int((now - self._last_beat) // self.period_s))

    def trust(self, now: float) -> float:
        """1.0 for a quick link, falling to 0 as heartbeats go missing or slow down."""
        if self._last_beat is None or self.missed(now) >= self.missed_to_leave:
            return 0.0
        if self.rtt_ms is None:
            return 0.5
        return round(max(0.2, min(1.0, 1.0 - (self.rtt_ms - 50.0) / 450.0)), 2)

    def update(self, now: float) -> str:
        poor = self.missed(now) >= self.missed_to_leave or (
            self.rtt_ms is not None and self.rtt_ms > self.slow_ms
        )
        if self.state == self.WIFI:
            if poor:
                self.state, self._good_since = self.FALLBACK, None
        elif poor or self._last_beat is None:
            self._good_since = None
        else:
            self._good_since = now if self._good_since is None else self._good_since
            if now - self._good_since >= self.back_s:
                self.state, self._good_since = self.WIFI, None
        return self.state


# ---- which radio would carry the alerts: Wi-Fi, or Bluetooth when Wi-Fi is gone


class RadioLink:
    """Alerts go over Wi-Fi. When Wi-Fi has been gone for leave_s seconds they would go over
    Bluetooth, if Bluetooth is on; with neither there is no link. Wi-Fi must be back for back_s
    seconds before the choice returns to it, so a flickering signal does not flip the link.

    The inputs are what the phone's browser reports (None means it cannot tell). The data path of
    the demo itself can be a cable, so turning Wi-Fi off does not cut the demo.
    """

    WIFI, BLUETOOTH, NONE, UNKNOWN = "wifi", "bluetooth", "none", "unknown"

    def __init__(self, leave_s=2.0, back_s=5.0):
        self.leave_s, self.back_s = leave_s, back_s
        self.state = self.UNKNOWN
        self._wifi_gone_since: float | None = None
        self._wifi_back_since: float | None = None

    def update(self, wifi: bool | None, bluetooth: bool | None, now: float) -> str:
        if wifi is None:
            self.state = self.UNKNOWN
            self._wifi_gone_since = self._wifi_back_since = None
            return self.state
        if self.state in (self.UNKNOWN, self.WIFI) and wifi:
            self.state = self.WIFI
            self._wifi_gone_since = None
        elif wifi:
            self._wifi_gone_since = None
            self._wifi_back_since = now if self._wifi_back_since is None else self._wifi_back_since
            if now - self._wifi_back_since >= self.back_s:
                self.state, self._wifi_back_since = self.WIFI, None
        else:
            self._wifi_back_since = None
            self._wifi_gone_since = now if self._wifi_gone_since is None else self._wifi_gone_since
            if self.state in (self.UNKNOWN, self.WIFI):
                if now - self._wifi_gone_since >= self.leave_s or self.state == self.UNKNOWN:
                    self.state = self.BLUETOOTH if bluetooth else self.NONE
        if self.state in (self.BLUETOOTH, self.NONE):
            self.state = self.BLUETOOTH if bluetooth else self.NONE
        return self.state


# ---- from phone readings to the decision layer's inputs


def tilt(ax: float, ay: float, az: float) -> tuple[float, float]:
    """Pitch and roll in radians from the gravity vector. A phone lying flat gives 0 and 0."""
    return math.atan2(-ax, math.hypot(ay, az)), math.atan2(ay, az)


def camera_trust_factor(brightness: float) -> float:
    """0 in the dark, 1 in good light; brightness is the mean image luminance, 0 to 1."""
    return max(0.0, min(1.0, (brightness - 0.05) / 0.25))


WORD_COMMANDS = {
    "stop": Command.STOP,
    "go": Command.GO,
    "on": Command.AUTOMATIC,
    "off": Command.MANUAL,
    "automatic": Command.AUTOMATIC,
    "manual": Command.MANUAL,
    "wheels": Command.WHEELS,
    "tracks": Command.TRACKS,
    "up": Command.SPEED_UP,
    "down": Command.SLOW_DOWN,
}


@dataclass
class Snapshot:
    """Everything the dashboard and the phone need, as plain data."""

    data: dict


class Middleware:
    LOG_EVERY_S = 0.5  # how often one source may add a line to the log
    VIBRATION_SLIP = 2.0  # m/s^2 of acceleration spread over one second counts as rough ground

    def __init__(self, core: DecisionCore | None = None, clock=time.monotonic):
        self.core = core or DecisionCore()
        self.clock = clock
        self.sensors = {s.id: SensorState(s) for s in SENSORS}
        self.position = PositionSource()
        self.link = LinkSelector()  # heartbeats: round trip and missed beats of the data path
        self.radio = RadioLink()
        self.wifi: bool | None = None
        self.bluetooth: bool | None = None
        self.log: deque = deque(maxlen=40)  # the common messages, newest last
        self.detections: list[dict] = []
        self.brightness = 1.0
        self.accuracy_m: float | None = None
        self._accel: deque = deque(maxlen=200)
        self._last_decision = None
        self._t0: float | None = (
            None  # the first time seen, so the dashboard shows seconds since start
        )
        self._last_say: dict[tuple[str, str], float] = {}
        self._stop_sent: float | None = None
        self.latency: dict[str, float] = {}

    # -- inputs from the phone

    def hello(self, caps: dict, now: float | None = None) -> None:
        """The phone tells which sensors its browser has. Unknown ids are ignored."""
        for sid, st in self.sensors.items():
            cap = caps.get(sid)
            if cap is None or st.info.always_unavailable:
                continue
            st.browser_has_it = bool(cap.get("available", True))
            st.why_not = (
                "" if st.browser_has_it else cap.get("why", "This browser does not provide it")
            )

    def _seen(self, sid: str, now: float, value: str, trust: float) -> None:
        st = self.sensors[sid]
        st.last_seen, st.value, st.trust = now, value, round(trust, 2)
        st.stamps.append(now)

    def _since_start(self, now: float) -> float:
        if self._t0 is None:
            self._t0 = now
        return now - self._t0

    def _say(self, now: float, source: str, kind: str, value: str, trust: float) -> None:
        """Add a line to the common-message log. Streams are thinned to a few lines a second so
        the log stays readable; commands are always kept."""
        key = (source, kind)
        last = self._last_say.get(key)
        if kind != "command" and last is not None and now - last < self.LOG_EVERY_S:
            return
        self._last_say[key] = now
        self.log.append(
            {
                "t": round(self._since_start(now), 1),
                "source": source,
                "kind": kind,
                "value": value,
                "trust": round(trust, 2),
            }
        )

    def on_motion(self, ax: float, ay: float, az: float, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        pitch, roll = tilt(ax, ay, az)
        self._accel.append((now, math.sqrt(ax * ax + ay * ay + az * az)))
        recent = [m for t, m in self._accel if now - t <= 1.0]
        spread = (max(recent) - min(recent)) if len(recent) > 3 else 0.0
        slip = spread > self.VIBRATION_SLIP
        self.core.on_motion(MotionReport(now, 0.9, pitch_rad=pitch, roll_rad=roll, wheel_slip=slip))
        deg = math.degrees(max(abs(pitch), abs(roll)))
        self._seen("motion", now, f"tilt {deg:.0f} deg, {'rough' if slip else 'smooth'}", 0.9)
        self._say(now, "motion", "motion", f"tilt {deg:.0f} deg, slip {slip}", 0.9)

    def on_gps(self, accuracy_m: float | None, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        self.accuracy_m = accuracy_m
        trust = 0.0 if accuracy_m is None else max(0.2, min(1.0, 1.0 - accuracy_m / 50.0))
        self._seen("gps", now, "no fix" if accuracy_m is None else f"+/-{accuracy_m:.0f} m", trust)
        self._say(
            now,
            "gps",
            "position",
            "no fix" if accuracy_m is None else f"+/-{accuracy_m:.0f} m",
            trust,
        )

    def on_light(self, brightness: float, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        self.brightness = brightness
        self._seen(
            "camera", now, f"light {brightness * 100:.0f} %", camera_trust_factor(brightness)
        )

    def on_heartbeat(self, rtt_ms: float | None, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        self.link.heartbeat(now, rtt_ms)

    def on_radio(self, wifi: bool | None, bluetooth: bool | None, now: float | None = None) -> None:
        """What the phone's browser says about its radios. None means it cannot tell."""
        now = self.clock() if now is None else now
        self.wifi, self.bluetooth = wifi, bluetooth
        if wifi is not None:
            self._seen("wifi", now, "connected" if wifi else "off", 0.9)
        if bluetooth is not None:
            self._seen("bluetooth", now, "on" if bluetooth else "off", 0.9)
        word = {True: "on", False: "off", None: "unknown"}
        self._say(now, "radio", "link", f"Wi-Fi {word[wifi]}, Bluetooth {word[bluetooth]}", 0.9)

    def on_microphone(self, now: float | None = None, value: str = "listening") -> None:
        now = self.clock() if now is None else now
        self._seen("microphone", now, value, 0.9)

    def on_word(
        self, word: str, confidence: float = 0.9, now: float | None = None
    ) -> Command | None:
        """A recognised word. Only the command words reach the decision layer."""
        now = self.clock() if now is None else now
        command = WORD_COMMANDS.get(word.strip().lower())
        self._seen("microphone", now, f'heard "{word.strip().lower()}"', confidence)
        if command is None:
            return None
        return self.on_command(command, confidence, now, source="voice")

    def on_command(
        self, command: Command, trust: float = 1.0, now: float | None = None, source: str = "button"
    ) -> Command:
        now = self.clock() if now is None else now
        self.core.on_command(command, trust)
        if command == Command.STOP:
            self._stop_sent = now
        self._say(now, source, "command", command.name, trust)
        return command

    def on_obstacle(
        self,
        label: str,
        distance_m: float,
        confidence: float,
        bearing_rad: float = 0.0,
        now: float | None = None,
    ) -> None:
        now = self.clock() if now is None else now
        trust = confidence * camera_trust_factor(self.brightness)
        self.core.on_obstacle(ObstacleReport(now, trust, Source.DETECTOR, distance_m, label))
        self._say(now, "detector", "obstacle", f"{label} {distance_m:.1f} m", trust)
        self.detections.append(
            {
                "t": now,
                "label": label,
                "distance_m": round(distance_m, 2),
                "bearing_rad": round(bearing_rad, 3),
                "trust": round(trust, 2),
            }
        )
        del self.detections[:-10]

    def on_ground(
        self,
        source: Source,
        ground: Ground,
        confidence: float,
        distance_m: float = math.nan,
        now: float | None = None,
    ) -> None:
        now = self.clock() if now is None else now
        trust = confidence * camera_trust_factor(self.brightness)
        self.core.on_ground(GroundReport(now, trust, source, ground, distance_m))
        self._say(now, source.name.lower(), "ground", ground.name.lower(), trust)

    # -- one decision and a snapshot of everything

    def fired_rule(self) -> str:
        for child in self.core.tree.children:
            if child.status.name == "SUCCESS":
                return child.name
        return ""

    def rule_names(self) -> list[str]:
        return [c.name for c in self.core.tree.children]

    def tick(self, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        pos = self.position.update(self.accuracy_m, now)
        link = self.radio.update(self.wifi, self.bluetooth, now)
        d = self.core.tick(now)
        self._last_decision = d
        if self._stop_sent is not None and d.stop:
            self.latency["voice_or_button_to_stop_ms"] = round((now - self._stop_sent) * 1000, 1)
            self._stop_sent = None
        sensors = []
        for sid, st in self.sensors.items():
            state = st.state(now)
            sensors.append(
                {
                    "id": sid,
                    "label": st.info.label,
                    "state": state.value,
                    "reason": st.reason(),
                    "value": st.value if state == State.ACTIVE else "",
                    "trust": st.trust if state == State.ACTIVE else 0.0,
                    "rate_hz": round(st.rate_hz(now), 1),
                }
            )
        return {
            "time": round(self._since_start(now), 1),
            "sensors": sensors,
            "messages": list(self.log)[-12:],
            "decision": {
                "mode": d.mode.name,
                "stop": d.stop,
                "speed": round(d.speed_limit, 2),
                "reason": d.reason,
                "priority": d.priority.name,
                "fired": self.fired_rule(),
            },
            "rules": self.rule_names(),
            "manual": not self.core.automatic,
            "position": {
                "source": pos,
                "accuracy_m": self.accuracy_m,
                "indoor_map_available": False,
            },
            "link": {
                "state": link,
                "wifi": self.wifi,
                "bluetooth": self.bluetooth,
                "rtt_ms": self.link.rtt_ms,
                "missed": self.link.missed(now),
                "trust": self.link.trust(now),
            },
            "detections": self.detections[-5:],
            "latency": dict(self.latency),
        }
