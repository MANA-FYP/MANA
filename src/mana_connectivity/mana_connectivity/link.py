"""Alert link (M3): which radio carries alerts to the caregiver, and an outbox so none is lost.
Pure Python, no ROS.

Each link proves itself with heartbeats, one every period_s seconds, answered by the other end.
Alerts go over Wi-Fi. Once Wi-Fi has missed missed_to_leave heartbeats in a row they go over
Bluetooth, if Bluetooth is up; with neither there is no link. While Bluetooth carries the alerts,
Wi-Fi must answer steadily for back_s seconds before they return to it, so a fading signal gives
one switch away and one back. When Bluetooth is not carrying them (at start, or when it is down
too), Wi-Fi takes them as soon as it answers. A heartbeat counts as missed only half a period
after it was due, so one lost beat followed by a late one does not switch.

An alert stays in the outbox until the other end acknowledges it. It is sent again every
resend_s seconds, and at once over the new link after a switch. A responder must acknowledge
alerts; if more than max_pending wait without an answer, the oldest is dropped and counted.
Alert ids carry a token for this boot, so a late answer from an earlier run cannot clear one.
"""

import secrets
from enum import IntEnum

MAX_MISSED = 255  # missed_heartbeats is a uint8 in LinkReport


class Link(IntEnum):  # the codes of mana_msgs/LinkReport
    WIFI = 0
    BLUETOOTH = 1


class Heartbeat:
    def __init__(self, period_s: float = 1.0):
        self.period_s = period_s
        self.last: float | None = None

    def beat(self, now: float) -> None:
        self.last = now

    def missed(self, now: float) -> int:
        """Heartbeats missed in a row; every one counts as missed before the first answer."""
        if self.last is None:
            return MAX_MISSED
        return min(MAX_MISSED, max(0, int((now - self.last) / self.period_s - 0.5)))


class LinkSelector:
    def __init__(self, missed_to_leave: int = 2, back_s: float = 5.0):
        self.missed_to_leave, self.back_s = missed_to_leave, back_s
        self.active: Link | None = None  # no radio until the first one answers
        self._steady_since: float | None = None

    def update(self, wifi_missed: int, bluetooth_missed: int, now: float) -> Link | None:
        wifi_up = wifi_missed < self.missed_to_leave
        bluetooth = Link.BLUETOOTH if bluetooth_missed < self.missed_to_leave else None
        if self.active == Link.BLUETOOTH and bluetooth and wifi_up:
            # Bluetooth carries the alerts: Wi-Fi must be steady for back_s first
            self._steady_since = now if self._steady_since is None else self._steady_since
            if now - self._steady_since >= self.back_s:
                self.active, self._steady_since = Link.WIFI, None
            return self.active
        self._steady_since = None
        self.active = Link.WIFI if wifi_up else bluetooth
        return self.active


class Outbox:
    def __init__(self, resend_s: float = 1.0, max_pending: int = 100, boot: str | None = None):
        self.resend_s, self.max_pending = resend_s, max_pending
        self.boot = boot or secrets.token_hex(3)
        self.pending: dict[str, str] = {}  # in the order they were added
        self._sent: dict[str, tuple[Link, float]] = {}  # alert id: over which link, and when
        self._next = 1
        self.dropped = 0

    def add(self, text: str) -> str:
        alert_id, self._next = f"{self.boot}-{self._next}", self._next + 1
        self.pending[alert_id] = text
        while len(self.pending) > self.max_pending:
            self.ack(next(iter(self.pending)))
            self.dropped += 1
        return alert_id

    def ack(self, alert_id: str) -> None:
        self.pending.pop(alert_id, None)
        self._sent.pop(alert_id, None)

    def due(self, link: Link | None, now: float) -> list[tuple[str, str]]:
        """The alerts to send over link now: new ones, ones sent over another link, and ones not
        acknowledged within resend_s. Nothing is sent without a link; it waits for one."""
        if link is None:
            return []
        out = []
        for alert_id, text in self.pending.items():
            sent = self._sent.get(alert_id)
            if sent is None or sent[0] != link or now - sent[1] >= self.resend_s:
                self._sent[alert_id] = (link, now)
                out.append((alert_id, text))
        return out
