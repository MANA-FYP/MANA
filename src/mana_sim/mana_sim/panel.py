"""The supervisor's panel (#80): what MANA decides during a test drive, and switches to break it.

Pure Python, no ROS: panel_node.py feeds it the messages and serves it on localhost. The view
shows who is driving, the mode, speed limit and stop with its reason, the message being said, the
position source, both alert links, the faults that are on, and the ground and obstacle reports,
split into those MANA trusts now and those it does not (too old, or trusted too little: the core's
own Limits). The switches are the simulation's faults (fault_node's set_fault) and the user's
STOP, GO, YES and NO, sent as a button press.
"""

from dataclasses import dataclass

from mana_core.decision import Command, Ground, Limits, Source

# (label, sensor, kind) as fault_node's set_fault takes them
FAULTS = (
    ("Lose GPS", "gps", "dropout"),
    ("Fade Wi-Fi", "wifi", "fade"),
    ("Darkness", "camera", "dark"),
    ("Cover the camera", "camera", "dropout"),
    ("Cover the 3D camera", "depth", "dropout"),
    ("Lidar off", "lidar", "dropout"),
    ("IMU off", "imu", "dropout"),
    ("Camera noise", "camera", "noise"),
    ("3D camera noise", "depth", "noise"),
    ("Lidar noise", "lidar", "noise"),
    ("IMU noise", "imu", "noise"),
    ("GPS noise", "gps", "noise"),
    ("Wheel slip", "wheels", "slip"),
)
COMMANDS = {"STOP": Command.STOP, "GO": Command.GO, "YES": Command.YES, "NO": Command.NO}
MODES = {0: "wheels", 1: "tracks"}
POSITION_SOURCES = {0: "GPS", 1: "lidar map", 2: "odometry"}
LINKS = {0: "Wi-Fi", 1: "Bluetooth"}


@dataclass(frozen=True)
class Report:
    stamp: float
    trust: float
    text: str


def ground_text(source: int, ground: int, distance_m: float) -> str:
    where = f" at {distance_m:.1f} m" if distance_m == distance_m and distance_m > 0 else ""
    return f"{_name(Source, source)}: {_name(Ground, ground).lower().replace('_', ' ')}{where}"


def obstacle_text(source: int, label: str, distance_m: float) -> str:
    return f"{_name(Source, source)}: {label or 'obstacle'} at {distance_m:.1f} m"


def _name(enum, code: int) -> str:
    try:
        return enum(code).name.replace("_", " ").capitalize()
    except ValueError:
        return f"code {code}"


class Panel:
    def __init__(self, limits: Limits | None = None):
        self.limits = limits or Limits()
        self.decision: dict | None = None
        self.said: dict | None = None
        self.driver: str | None = None  # who drives, when a driver is selected (driver:=true)
        self.position: dict | None = None
        self.links: dict[str, dict] = {}
        self.reports: dict[str, Report] = {}  # the latest per sensor and kind
        self.faults = "none"

    def on_decision(self, now, mode, stop, speed_limit, reason):
        self.decision = {
            "at": now,
            "mode": MODES.get(mode, f"code {mode}"),
            "stop": bool(stop),
            "speed_limit": round(float(speed_limit), 2),
            "reason": reason,
        }

    def on_message(self, now, text, urgent):
        self.said = {"at": now, "text": text, "urgent": bool(urgent)}

    def on_driver(self, name):
        self.driver = name

    def on_position(self, stamp, source, trust, error_m):
        self.position = {
            "stamp": stamp,
            "source": POSITION_SOURCES.get(source, f"code {source}"),
            "trust": trust,
            "error_m": error_m,
        }

    def on_link(self, stamp, link, connected, missed):
        self.links[LINKS.get(link, f"code {link}")] = {
            "stamp": stamp,
            "connected": bool(connected),
            "missed": int(missed),
        }

    def on_report(self, key, stamp, trust, text):
        self.reports[key] = Report(stamp, trust, text)

    def on_faults(self, summary):
        self.faults = summary

    def view(self, now: float) -> dict:
        lim = self.limits
        trusted, doubtful = [], []
        for r in sorted(self.reports.values(), key=lambda r: r.text):
            age = now - r.stamp
            why = []
            if r.trust < lim.min_trust:
                why.append(f"trust {r.trust:.2f}")
            if not -lim.max_age_s <= age <= lim.max_age_s:
                why.append(f"{age:.1f} s old")
            row = {"text": r.text, "trust": round(r.trust, 2)}
            (doubtful if why else trusted).append({**row, "why": ", ".join(why)} if why else row)
        position = None
        if self.position:
            age = now - self.position["stamp"]
            position = {
                "source": self.position["source"],
                "error_m": round(self.position["error_m"], 1),
                "lost": not 0.0 <= age <= lim.position_max_age_s,
            }
        links = {
            name: {
                "usable": link["connected"]
                and link["missed"] < lim.missed_heartbeats
                and 0.0 <= now - link["stamp"] <= lim.link_max_age_s,
                "missed": link["missed"],
            }
            for name, link in sorted(self.links.items())
        }
        return {
            "now": round(now, 1),
            "driver": self.driver or "MANA's host (no driver selected)",
            "decision": self.decision,
            "said": self.said,
            "position": position,
            "links": links,
            "faults": self.faults,
            "trusted": trusted,
            "doubtful": doubtful,
            "switches": {
                "faults": [
                    {"label": label, "on": f"{sensor} {kind}" in self.faults.split(", ")}
                    for label, sensor, kind in FAULTS
                ],
                "commands": list(COMMANDS),
            },
        }


def fault(label: str) -> tuple[str, str] | None:
    """The (sensor, kind) of a fault switch, or None for a label that is not one."""
    return next(((s, k) for name, s, k in FAULTS if name == label), None)
