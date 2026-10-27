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
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from mana_core.decision import (
    Command,
    DecisionCore,
    Ground,
    GroundReport,
    Mode,
    MotionReport,
    ObstacleReport,
    Priority,
    Source,
)
from mana_core.messages import Message, MessageQueue, direction

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
    SensorInfo("depth", "Depth", "Not available"),
    SensorInfo("lidar", "Lidar", "Not available"),
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
    switched_off: bool = False  # the user switched this sensor off on the phone
    stamps: deque = field(default_factory=lambda: deque(maxlen=200))

    def rate_hz(self, now: float, window_s: float = 2.0) -> float:
        return sum(1 for t in self.stamps if now - t <= window_s) / window_s

    def state(self, now: float) -> State:
        if self.info.always_unavailable or not self.browser_has_it:
            return State.UNAVAILABLE
        if self.switched_off:
            return State.IDLE
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

USER_STOP_TEXT = "MANA deactivated. Press GO or say go to restart."
# how urgent each kind of line is, when the core does not give a priority of its own
KIND_PRIORITY = {
    "link": Priority.WARNING,
    "position": Priority.WARNING,
    "mode": Priority.INFO,
    "switch": Priority.INFO,
    "coverage": Priority.INFO,
}


COMMAND_TEXT = {
    Command.STOP: "Stop",
    Command.GO: "Go",
    Command.AUTOMATIC: "Automatic mode",
    Command.MANUAL: "Manual mode",
    Command.WHEELS: "Use wheels",
    Command.TRACKS: "Use tracks",
    Command.SPEED_UP: "Speed up",
    Command.SLOW_DOWN: "Slow down",
}


# What each sensor gives MANA, and what is lost without it. The person using the device is not
# the one running the sensors, so the wording never says who switched them.
AVAILABLE_TEXT = {
    "camera": "MANA can see obstacles and the ground.",
    "motion": "MANA can check tilt and rough ground.",
    "gps": "Using GPS for position.",
    "microphone": "Voice commands are on.",
    "bluetooth": "It is the backup link for alerts.",
    "wifi": "Alerts use Wi-Fi.",
}
LOST_TEXT = {
    "camera": "MANA cannot see obstacles or the ground.",
    "motion": "MANA cannot check tilt or rough ground.",
    "gps": "Using the indoor map for position.",
    "microphone": "Voice commands are off, the buttons still work.",
    "bluetooth": "There is no backup link for alerts.",
    "wifi": "",
}

LINK_TEXT = {
    "wifi": "Wi-Fi is available. Alerts use Wi-Fi.",
    "bluetooth": "Wi-Fi is not available. Switching alerts to Bluetooth.",
    "none": "Wi-Fi and Bluetooth are off. There is no alert link.",
}

# When a sensor is tested on its own: what MANA can and cannot decide with it. The radios only
# choose the alert link, so they are not counted here.
SOLO_TEXT = {
    "camera": "Only the camera is on. MANA can see obstacles, but one camera cannot confirm a step "
    "or check the tilt, so it cannot choose wheels or tracks. It slows down to stay safe.",
    "motion": "Only motion is on. MANA can check tilt and rough ground, and rough ground changes "
    "it to tracks, but it cannot see obstacles or steps. It slows down to stay safe.",
    "gps": "Only GPS is on. MANA can choose the position source, but it has nothing to decide how "
    "to move. It slows down to stay safe.",
    "microphone": "Only the microphone is on. MANA obeys stop and go, but it cannot see or feel "
    "the ground. It slows down to stay safe.",
}
NO_SENSOR_TEXT = "No sensor is on. MANA cannot make a decision, so it slows down to stay safe."
SHOWN_DISTANCE_M = 8.0  # camera distances beyond this are too rough to write down


def sensor_text(label: str, reason: str) -> str:
    """One sentence for a sensor that is not available."""
    why = reason.strip().lower()
    if why in ("", "not available"):
        return f"{label} is not available."
    return f"{label} is not available: {why}."


def not_available(sid: str, label: str, reason: str = "") -> str:
    """The sentence for a sensor that is gone, with what MANA loses."""
    return f"{sensor_text(label, reason)} {LOST_TEXT.get(sid, '')}".strip()


def available(sid: str, label: str) -> str:
    return f"{label} is available. {AVAILABLE_TEXT.get(sid, '')}".strip()


def end_sentence(text: str) -> str:
    return text if text[-1:] in ".!?" else text + "."


@dataclass
class Snapshot:
    """Everything the dashboard and the phone need, as plain data."""

    data: dict


class Middleware:
    LOG_EVERY_S = 0.5  # how often one source may add a line to the log
    VIBRATION_SLIP = 2.0  # m/s^2 of acceleration spread over one second counts as rough ground

    def __init__(
        self, core: DecisionCore | None = None, clock=time.monotonic, directions: bool = False
    ):
        self.core = core or DecisionCore()
        # what the user hears: one message at a time, the most urgent first (mana_core.messages)
        self.queue = MessageQueue()
        self.directions = directions  # also say what is ahead, for a cane or glasses host
        self.heard: deque = deque(maxlen=10)  # what the queue let out, newest last
        self._heard_n = 0
        self.clock = clock
        self.sensors = {s.id: SensorState(s) for s in SENSORS}
        self.position = PositionSource()
        self.link = LinkSelector()  # heartbeats: round trip and missed beats of the data path
        self.radio = RadioLink()
        self.wifi: bool | None = None
        self.bluetooth: bool | None = None
        self.log: deque = deque(maxlen=40)  # the common messages, newest last
        self.inputs: deque = deque(maxlen=20)  # what the user said into the microphone, as text
        self.decisions: deque = deque(maxlen=30)  # every decision MANA took, as text
        self._decision_n = 0
        self._decision_key: tuple | None = None
        self._last_link: str | None = None
        self._last_position: str | None = None
        self._last_manual: bool | None = None
        self._sensor_state: dict[str, str] = {}
        self._sensor_pending: dict[str, tuple[str, float]] = {}
        self._was_user_stop = False
        self._reactivated_until = -1.0
        self._ever_active: set[str] = set()
        self._available_since: dict[str, float] = {}
        self._said_at: dict[str, float] = {}
        self._speaking: tuple[str, float] | None = None  # the phrase being spoken right now
        self.detections: list[dict] = []
        self._seen_candidate: tuple[frozenset | None, int] = (None, 0)
        self._seen_logged: frozenset | None = None
        self._last_active: set[str] | None = None
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

    def _decide(self, now: float, kind: str, text: str, priority: Priority | None = None) -> None:
        """Write down one decision MANA took, as a sentence. The same sentence is not written
        again within 10 s, so a radio that flickers does not repeat itself. Everything but a
        detection also goes into the message queue, which says one at a time, urgent first."""
        if kind in ("switch", "link", "position", "coverage"):  # stop and go may repeat
            last = self._said_at.get(text)
            if last is not None and now - last < 10.0:
                return
            self._said_at[text] = now
        self._decision_n += 1
        self.decisions.append(
            {
                "n": self._decision_n,
                "t": round(self._since_start(now), 1),
                "kind": kind,
                "text": text,
            }
        )
        if kind != "detection":  # a detection is shown as a box, not said
            level = priority if priority is not None else KIND_PRIORITY.get(kind, Priority.WARNING)
            self.queue.push(Message(text, level, True, now), now)

    def _sentence(self, d, fired: str, now: float) -> tuple[str, bool]:
        """The decision as one sentence, and whether the stop came from the user."""
        user_stop = d.stop and fired == "user stop"
        if user_stop:
            return USER_STOP_TEXT, True
        if d.stop:
            return f"Stopped. {end_sentence(d.reason)}", False
        how = "tracks" if d.mode == Mode.TRACKS else "wheels"
        lead = f"Driving on {how} at {round(d.speed_limit * 100)} percent speed."
        prefix = "MANA reactivated. " if now < self._reactivated_until else ""
        if not self.core.automatic:
            prefix += "Manual mode. "
        return f"{prefix}{lead} {end_sentence(d.reason)}", False

    def _standing(
        self, now: float, sentence: str, link: str, pos: str, sensors: list[dict]
    ) -> list[str]:
        """Everything MANA has decided that is still true, as sentences for the action box."""
        lines = [sentence]
        if link in ("bluetooth", "none"):
            lines.append(LINK_TEXT[link])
        if pos == PositionSource.INDOOR:
            lines.append("GPS is poor. Using the indoor map for position.")
        for s in sensors:
            sid = s["id"]
            if s["state"] == "active":
                self._ever_active.add(sid)
            if self.sensors[sid].info.always_unavailable:
                continue
            if sid in ("wifi", "bluetooth") and link in ("bluetooth", "none"):
                continue  # the link sentence already says it
            if self.sensors[sid].switched_off:
                lines.append(not_available(sid, s["label"]))
            elif s["state"] == "unavailable":
                lines.append(not_available(sid, s["label"], s["reason"]))
            elif s["state"] == "idle" and sid in self._ever_active:
                lines.append(not_available(sid, s["label"]))
            elif now - self._available_since.get(sid, -99.0) < 6.0:
                lines.append(available(sid, s["label"]))
        recent = [x for x in self.detections if now - x["t"] < 3.0]
        if recent:
            last = recent[-1]
            lines.append(f"Obstacle detected: {last['label']}, about {last['distance_m']} m ahead.")
        return lines

    def _watch(self, now: float, d, fired: str, link: str, pos: str, sensors: list[dict]) -> str:
        """Write every change as text: the decision, the alert link, the position source, manual
        or automatic, and each sensor going on or off. Returns the decision sentence."""
        sentence, user_stop = self._sentence(d, fired, now)
        if self._was_user_stop and not d.stop:
            self._reactivated_until = now + 6.0
            sentence, user_stop = self._sentence(d, fired, now)
        self._was_user_stop = user_stop

        key = (user_stop, d.stop, d.mode.name, re.sub(r"[\d.]+", "#", d.reason))
        if key != self._decision_key:
            self._decision_key = key
            self._decide(now, "decision", sentence, d.priority)

        if link != self._last_link:
            if link != "unknown":
                text = LINK_TEXT[link]
                if link == "wifi" and self._last_link not in (None, "unknown"):
                    text = "Wi-Fi is available again. Alerts use Wi-Fi."
                self._decide(now, "link", text)
            self._last_link = link

        if pos != self._last_position:
            if self._last_position is not None:
                indoor = pos == PositionSource.INDOOR
                self._decide(
                    now,
                    "position",
                    "GPS is poor. Using the indoor map for position."
                    if indoor
                    else "GPS is good again. Using GPS for position.",
                )
            self._last_position = pos

        manual = not self.core.automatic
        if manual != self._last_manual:
            if self._last_manual is not None:
                self._decide(
                    now,
                    "mode",
                    "Manual mode. You are in control of the device."
                    if manual
                    else "Automatic mode. MANA is choosing the action.",
                )
            self._last_manual = manual

        # a sensor must hold its new state for 1.5 s before it is written down
        for s in sensors:
            sid = s["id"]
            if self.sensors[sid].info.always_unavailable:
                continue
            shown = self._sensor_state.setdefault(sid, s["state"])
            if s["state"] == shown:
                self._sensor_pending.pop(sid, None)
                continue
            cand = self._sensor_pending.get(sid)
            if cand is None or cand[0] != s["state"]:
                self._sensor_pending[sid] = (s["state"], now)
            elif now - cand[1] >= 1.5:
                self._sensor_pending.pop(sid)
                self._sensor_state[sid] = s["state"]
                label = s["label"]
                if s["state"] == "active":
                    self._available_since[sid] = now
                if sid == "wifi":
                    continue  # the alert-link rule says it, once, with what it means
                if sid == "bluetooth" and s["state"] != "active" and self.wifi is False:
                    continue  # both radios gone: the link rule says it
                if s["state"] == "active":
                    text = available(sid, label)
                elif s["state"] == "unavailable":
                    text = not_available(sid, label, s["reason"])
                else:
                    text = not_available(sid, label)
                self._decide(now, "switch", text)

        # one sensor on its own: say what MANA can and cannot decide with it
        active = {sid for sid in SOLO_TEXT if self._sensor_state.get(sid) == "active"}
        if active != self._last_active:
            if len(active) == 1:
                self._decide(now, "coverage", SOLO_TEXT[next(iter(active))])
            elif not active and self._last_active:
                self._decide(now, "coverage", NO_SENSOR_TEXT)
            self._last_active = active
        if next(s["state"] for s in sensors if s["id"] == "camera") != "active":
            self._seen_logged, self._seen_candidate = None, (None, 0)  # say it afresh next time
        return sentence

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
        self._seen("motion", now, f"{deg:.0f}° {'rough' if slip else 'smooth'}", 0.9)
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
        # a radio that is switched off is shown as not available, not as a live value
        for sid, on, why in (
            ("wifi", wifi, "Not available"),
            ("bluetooth", bluetooth, "Turned off"),
        ):
            if on is None:
                continue
            st = self.sensors[sid]
            st.browser_has_it, st.why_not = on, ("" if on else why)
            if on:
                self._seen(sid, now, "connected" if sid == "wifi" else "on", 0.9)
        word = {True: "on", False: "off", None: "unknown"}
        self._say(now, "radio", "link", f"Wi-Fi {word[wifi]}, Bluetooth {word[bluetooth]}", 0.9)

    def on_sensor_switch(self, sid: str, on: bool, now: float | None = None) -> None:
        """The user tapped a sensor tile on the phone to switch that sensor off or on."""
        now = self.clock() if now is None else now
        st = self.sensors.get(sid)
        if st is None or st.info.always_unavailable:
            return
        if on:
            st.switched_off = False
            return
        st.switched_off = True
        self._available_since.pop(sid, None)
        self._sensor_state[sid] = "idle"  # said here, so it is not said again as "stopped sending"
        self._sensor_pending.pop(sid, None)
        if sid == "gps":
            self.accuracy_m = None  # no fix: the position rule moves to the indoor map
        if sid != "wifi" and not (sid == "bluetooth" and self.wifi is False):
            self._decide(now, "switch", not_available(sid, st.info.label))

    def on_microphone(self, now: float | None = None, value: str = "listening") -> None:
        now = self.clock() if now is None else now
        self._seen("microphone", now, value, 0.9)

    MIC_ERRORS = {
        "not-allowed": "Permission denied",
        "service-not-allowed": "Permission denied",
        "network": "Needs internet",
        "audio-capture": "No microphone",
        "language-not-supported": "Language not supported",
    }

    def on_microphone_state(self, state: str, now: float | None = None) -> None:
        """The phone says whether its recogniser is listening, or why it is not."""
        now = self.clock() if now is None else now
        st = self.sensors["microphone"]
        if state == "listening":
            st.browser_has_it, st.why_not = True, ""
            self._seen("microphone", now, "listening", 0.9)
        else:
            st.browser_has_it = False
            st.why_not = self.MIC_ERRORS.get(state, "Not working")

    def on_heard(self, text: str, now: float | None = None, final: bool = True) -> None:
        """A phrase from the browser recogniser. While it is still being spoken (final is false)
        it only fills the live line; a finished phrase is kept as what the user said."""
        now = self.clock() if now is None else now
        text = text.strip().lower()
        if not final:
            if text:
                self._speaking = (text, now)
                self._seen("microphone", now, "hearing you", 0.9)
            return
        self._speaking = None
        if not text:
            return
        found = [COMMAND_TEXT[WORD_COMMANDS[w]] for w in text.split() if w in WORD_COMMANDS]
        self.inputs.append(
            {"t": round(self._since_start(now), 1), "text": text, "command": ", ".join(found)}
        )

    def on_word(
        self,
        word: str,
        confidence: float = 0.9,
        now: float | None = None,
        record: bool = True,
    ) -> Command | None:
        """A recognised word. Only the command words reach the decision layer."""
        now = self.clock() if now is None else now
        command = WORD_COMMANDS.get(word.strip().lower())
        heard = word.strip().lower()
        self._seen("microphone", now, f'heard "{heard}"', confidence)
        if record and heard:
            said = COMMAND_TEXT.get(command, "") if command else ""
            self.inputs.append(
                {"t": round(self._since_start(now), 1), "text": heard, "command": said}
            )
        if command is None:
            return None
        return self.on_command(command, confidence, now, source="voice")

    def on_command(
        self, command: Command, trust: float = 1.0, now: float | None = None, source: str = "button"
    ) -> Command:
        now = self.clock() if now is None else now
        already_stopped = self.core.stopped_by_user
        self.core.on_command(command, trust)
        if command == Command.STOP:
            self._stop_sent = now
            if already_stopped:  # answered again, so the user knows this stop was heard too
                self._decide(now, "decision", USER_STOP_TEXT, Priority.URGENT)
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
        # not written to the decision log: a detection is shown as a box on the camera, and the
        # log says "Obstacle ahead, slowing down" or "stopping" once the core acts on it
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

    def on_detections(
        self, found: list[tuple[str, float | None]], now: float | None = None
    ) -> None:
        """Everything the detector found in one frame, as (label, distance or None). Written to
        the log when what the camera sees changes and holds for two frames, so it is neither
        repeated nor set off by one flickering frame."""
        now = self.clock() if now is None else now
        nearest: dict[str, float | None] = {}
        for label, dist in found:
            old = nearest.get(label)
            nearest[label] = dist if old is None or (dist is not None and dist < old) else old
        key = frozenset(nearest)
        seen, count = self._seen_candidate
        self._seen_candidate = (key, count + 1 if key == seen else 1)
        if self._seen_candidate[1] < 2 or key == self._seen_logged:
            return
        if key or self._seen_logged:
            if key:
                order = sorted(nearest.items(), key=lambda kv: (kv[1] is None, kv[1] or 0.0, kv[0]))
                # a label goes without a distance when the estimate is too far to trust (a box
                # cut off by the frame edge looks far away) or when there is none: a class with
                # no typical height, or a box below the confidence that to_obstacles keeps
                things = ", ".join(
                    f"{label} (about {dist:.1f} m)"
                    if dist is not None and dist <= SHOWN_DISTANCE_M
                    else label
                    for label, dist in order[:5]
                )
                self._decide(now, "detection", f"Camera sees: {things}.")
            else:
                self._decide(now, "detection", "The camera sees nothing it knows.")
        self._seen_logged = key

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
                    "value": st.value
                    if state == State.ACTIVE
                    else ("Switched off" if st.switched_off else ""),
                    "trust": st.trust if state == State.ACTIVE else 0.0,
                    "rate_hz": round(st.rate_hz(now), 1),
                }
            )
        sentence = self._watch(now, d, self.fired_rule(), link, pos, sensors)
        if self.directions and (ahead := direction(self.core, now)):
            self.queue.push(ahead, now)
        if said := self.queue.release(now):
            self._heard_n += 1
            self.heard.append(
                {
                    "n": self._heard_n,
                    "t": round(self._since_start(now), 1),
                    "text": said.text,
                    "priority": said.priority.name,
                    "speak": said.speak,
                    "kind": said.kind,
                }
            )
        lines = self._standing(now, sentence, link, pos, sensors)
        speaking = ""
        if self._speaking is not None and now - self._speaking[1] < 4.0:
            speaking = self._speaking[0]
        return {
            "time": round(self._since_start(now), 1),
            "sensors": sensors,
            "decisions": list(self.decisions),
            "heard": list(self.heard),
            "speaking": speaking,
            "messages": list(self.log)[-12:],
            "inputs": list(self.inputs),
            "decision": {
                "mode": d.mode.name,
                "stop": d.stop,
                "speed": round(d.speed_limit, 2),
                "reason": d.reason,
                "sentence": sentence,
                "lines": lines,
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
