"""What the user hears: one message at a time, the most urgent first, and step directions.

The decision core makes a message every time its reason changes. Said as they come, an old
"Level ground" could be spoken before a new "Drop ahead, stopping", and two warnings could talk over
each other. The queue fixes that: it lets one message out at a time, urgent ones first, and it drops
what is no longer true. `direction` adds a short sentence about what is ahead, for a host with no
chair to drive, such as a smart cane or glasses. Pure Python, no ROS.
"""

import math
from dataclasses import dataclass

from mana_core.decision import STEPS, DecisionCore, Ground, Priority

DECISION = "decision"
DIRECTION = "direction"


@dataclass(frozen=True)
class Message:
    text: str
    priority: Priority
    speak: bool  # also say it aloud, not only show it
    stamp: float  # seconds, when it was made
    kind: str = DECISION  # DECISION or DIRECTION


class MessageQueue:
    """Lets one message out at a time.

    - The most urgent message goes first; the same priority goes oldest first.
    - A new message waits until the one before it has been said (its length at chars_per_s, and at
      least gap_s). An URGENT message does not wait for a less urgent one: it goes at once and
      replaces every waiting message that is less urgent. It does wait for an URGENT one, so a stop
      never cuts off another stop.
    - A message older than max_age_s is dropped, because it may no longer be true.
    - A message is not said twice in a row: the same decision text again within max_age_s, or the
      same direction as the last one, is dropped. An URGENT decision is always said again, so a
      second STOP is answered. An URGENT direction is also dropped when an
      URGENT message went out less than max_age_s ago: the user has been told to stop already.
    """

    def __init__(self, gap_s: float = 1.5, max_age_s: float = 6.0, chars_per_s: float = 14.0):
        self.gap_s = gap_s
        self.max_age_s = max_age_s
        self.chars_per_s = chars_per_s
        self.waiting: list[Message] = []
        self._free_at = -math.inf  # when the message that went out last has been said
        self._last: dict[str, tuple[str, float]] = {}  # kind -> (text, when it went out)
        self._last_urgent_at = -math.inf  # when an URGENT message went out last
        self._last_priority = Priority.INFO  # of the message that went out last

    def push(self, message: Message, now: float) -> None:
        if any(w.text == message.text for w in self.waiting):
            return
        if self._repeats(message):
            return
        self.waiting.append(message)

    def _repeats(self, message: Message) -> bool:
        if (
            message.kind == DIRECTION
            and message.priority == Priority.URGENT
            and message.stamp - self._last_urgent_at <= self.max_age_s
        ):
            return True  # a stop has just been said
        if message.kind == DECISION and message.priority == Priority.URGENT:
            return False  # a stop is answered every time
        last = self._last.get(message.kind)
        if last is None or last[0] != message.text:
            return False
        if message.kind == DIRECTION:
            return True  # said again only after the direction has changed
        return message.stamp - last[1] <= self.max_age_s

    def release(self, now: float) -> Message | None:
        """The message to say now, or None. Call it once per tick."""
        self.waiting = [m for m in self.waiting if now - m.stamp <= self.max_age_s]
        if not self.waiting:
            return None
        best = max(self.waiting, key=lambda m: (m.priority, -m.stamp))
        if now < self._free_at and not (
            best.priority == Priority.URGENT and self._last_priority < Priority.URGENT
        ):
            return None
        self.waiting.remove(best)
        if best.priority == Priority.URGENT:
            # less urgent ones are replaced; a waiting urgent direction would only repeat the stop
            self.waiting = [
                m for m in self.waiting if m.priority >= Priority.URGENT and m.kind != DIRECTION
            ]
        spoken_s = len(best.text) / self.chars_per_s if best.speak else 0.0
        self._free_at = now + max(self.gap_s, spoken_s)
        self._last[best.kind] = (best.text, now)
        self._last_priority = best.priority
        if best.priority == Priority.URGENT:
            self._last_urgent_at = now
        return best


def metres(distance_m: float) -> str:
    """A distance a person can use: whole metres, and "less than a metre" when it is closer."""
    if distance_m < 1.0:
        return "less than a metre"
    d = round(distance_m)
    return "1 metre" if d == 1 else f"{d} metres"


def direction(core: DecisionCore, now: float) -> Message | None:
    """The one thing most worth telling the user about what is ahead, or None.

    Only reports the core trusts and that are not too old count. A drop or an obstacle at stopping
    distance is URGENT; anything else on the way within the core's reaction distances is a WARNING.
    The nearest of the most urgent hazards wins.
    """
    lim = core.limits

    def good(report) -> bool:
        return now - report.stamp <= lim.max_age_s and report.trust >= lim.min_trust

    found: list[tuple[Priority, float, str]] = []  # priority, distance, sentence
    for r in (r for r in core.ground.values() if good(r)):
        near = not r.distance_m > lim.step_m  # an unknown distance counts as near
        if not near or r.ground not in STEPS | {Ground.DROP, Ground.RAMP}:
            continue
        known = not math.isnan(r.distance_m)
        where = f", {metres(r.distance_m)}" if known else ""
        if r.ground == Ground.DROP:
            urgent = known and r.distance_m <= lim.edge_m
            found.append(
                (
                    Priority.URGENT if urgent else Priority.WARNING,
                    r.distance_m,
                    f"Drop ahead{where}. Stop.",
                )
            )
        elif r.ground == Ground.STAIRS_DOWN:
            found.append((Priority.WARNING, r.distance_m, f"Steps down ahead{where}. Stop."))
        elif r.ground == Ground.STAIRS_UP:
            found.append((Priority.WARNING, r.distance_m, f"Steps up ahead{where}."))
        elif r.ground == Ground.CURB:
            found.append((Priority.WARNING, r.distance_m, f"Curb ahead{where}."))
        else:
            found.append((Priority.WARNING, r.distance_m, f"Ramp ahead{where}."))
    for r in (r for r in core.obstacles.values() if good(r)):
        if r.distance_m > lim.obstacle_slow_m:
            continue
        label = (r.label or "obstacle").capitalize()
        if r.distance_m <= lim.obstacle_stop_m:
            found.append(
                (Priority.URGENT, r.distance_m, f"Stop. {label} ahead, {metres(r.distance_m)}.")
            )
        else:
            found.append(
                (Priority.WARNING, r.distance_m, f"{label} ahead, {metres(r.distance_m)}.")
            )
    if not found:
        return None
    # most urgent first, then the nearest; an unknown distance (NaN) sorts last
    priority, _, text = min(found, key=lambda f: (-f[0], math.inf if math.isnan(f[1]) else f[1]))
    return Message(text, priority, True, now, DIRECTION)
