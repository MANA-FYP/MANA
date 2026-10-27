"""What the user hears: one message at a time, the most urgent first, and step directions.

The decision core makes a message every time its reason changes. Said as they come, an old
"Level ground" could be spoken before a new "Drop ahead, stopping", and two warnings could talk over
each other. The queue fixes that: it lets one message out at a time, urgent ones first, and it drops
what is no longer true. `direction` adds a short sentence about what is ahead, for a host with no
chair to drive, such as a smart cane or glasses. Pure Python, no ROS.

Contract with the host that says the messages: an URGENT message means "interrupt what you are
saying and say this now" (the dashboard does it with speechSynthesis.cancel()). A message that is
not URGENT is said after the one before it has finished.
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

    - The most urgent message goes first. The same priority goes decision first, then oldest first:
      what the core has just decided is newer than a direction that was pushed every tick.
    - Only the latest decision is true. When a decision is pushed, the decisions and the directions
      that are still waiting are dropped; an URGENT message (a stop) stays and is still said.
    - A new message waits until the one before it has been said (its length at chars_per_s, and at
      least gap_s). An URGENT message does not wait for a less urgent one: it goes at once and
      replaces every waiting message that is less urgent. It does wait for an URGENT one, so a stop
      never cuts off another stop.
    - A message older than max_age_s is dropped, because it may no longer be true. An URGENT one is
      never dropped for its age: a stop is always answered.
    - A message is not said twice in a row. A decision is a repeat when it is the same text as the
      decision pushed before it (within max_age_s), or as the one said last. A direction is a repeat
      until it has changed or `direction_gone` says that nothing is ahead any more. An URGENT
      decision is always said again, so a second STOP is answered. An URGENT direction is dropped
      when an URGENT message went out less than max_age_s ago: the user has been told to stop.
    - If the clock goes back (a simulation or a bag was reset) the queue starts again.
    """

    def __init__(self, gap_s: float = 1.5, max_age_s: float = 6.0, chars_per_s: float = 14.0):
        if gap_s >= max_age_s:
            raise ValueError(
                "gap_s must be shorter than max_age_s, or every queued message expires"
            )
        self.gap_s = gap_s
        self.max_age_s = max_age_s
        self.chars_per_s = chars_per_s
        self.waiting: list[Message] = []
        self._reset()

    def _reset(self) -> None:
        self.waiting.clear()
        self._now = -math.inf  # the latest time the queue has seen
        self._free_at = -math.inf  # when the message that went out last has been said
        self._pushed: dict[str, tuple[str, float]] = {}  # kind -> the last text pushed, and when
        self._said: dict[str, tuple[str, float]] = {}  # kind -> the last text said, and when
        self._last_urgent_at = -math.inf  # when an URGENT message went out last
        self._last_priority = Priority.INFO  # of the message that went out last

    def _clock(self, now: float) -> None:
        if now < self._now:
            self._reset()
        self._now = now

    def direction_gone(self) -> None:
        """Nothing is ahead any more: the same direction may be said again if it comes back."""
        self._pushed.pop(DIRECTION, None)
        self._said.pop(DIRECTION, None)

    def push(self, message: Message, now: float) -> None:
        self._clock(now)
        if any(w.text == message.text for w in self.waiting):
            return
        if self._repeats(message):
            return
        if message.kind == DECISION:
            # the newest decision is the only true one; a stop that is waiting is still answered
            dropped = [w for w in self.waiting if w.priority < Priority.URGENT]
            self.waiting = [w for w in self.waiting if w.priority >= Priority.URGENT]
            if any(w.kind == DIRECTION for w in dropped):
                self.direction_gone()  # what was dropped may come back at the next tick
        self._pushed[message.kind] = (message.text, message.stamp)
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
        last = self._pushed.get(message.kind)
        if last is None or last[0] != message.text:
            return False
        if message.kind == DIRECTION:
            return True  # said again only after the direction has changed or gone
        return message.stamp - last[1] <= self.max_age_s

    def _expire(self, now: float) -> None:
        kept = [
            m
            for m in self.waiting
            if m.priority == Priority.URGENT or now - m.stamp <= self.max_age_s
        ]
        if any(m.kind == DIRECTION for m in self.waiting) and not any(
            m.kind == DIRECTION for m in kept
        ):
            self.direction_gone()  # a direction that never got said may be pushed again
        self.waiting = kept

    def release(self, now: float) -> Message | None:
        """The message to say now, or None. Call it once per tick."""
        self._clock(now)
        self._expire(now)
        while self.waiting:
            best = max(self.waiting, key=lambda m: (m.priority, m.kind == DECISION, -m.stamp))
            if now < self._free_at and not (
                best.priority == Priority.URGENT and self._last_priority < Priority.URGENT
            ):
                return None
            self.waiting.remove(best)
            said = self._said.get(best.kind)
            if (
                best.kind == DECISION
                and best.priority < Priority.URGENT
                and said is not None
                and said[0] == best.text
                and now - said[1] <= self.max_age_s
            ):
                continue  # the same decision has just been said: nothing changed for the user
            if best.priority == Priority.URGENT:
                # less urgent ones are replaced; a waiting urgent direction would repeat the stop
                self.waiting = [
                    m for m in self.waiting if m.priority >= Priority.URGENT and m.kind != DIRECTION
                ]
            spoken_s = len(best.text) / self.chars_per_s if best.speak else 0.0
            self._free_at = now + max(self.gap_s, spoken_s)
            self._said[best.kind] = (best.text, now)
            self._last_priority = best.priority
            if best.priority == Priority.URGENT:
                self._last_urgent_at = now
            return best
        return None


def metres(distance_m: float) -> str:
    """A distance a person can use: whole metres, rounded down so that a hazard never sounds
    farther than it is, and "less than a metre" when it is closer."""
    if distance_m < 1.0:
        return "less than a metre"
    d = math.floor(distance_m)
    return "1 metre" if d == 1 else f"{d} metres"


def direction(core: DecisionCore, now: float) -> Message | None:
    """The one thing most worth telling the user about what is ahead, or None.

    Only reports the core trusts and that are not too old count. A step (stairs or a curb) is told
    only when the core confirms it with two different sensors, so the direction never says as a fact
    what the decision calls "a possible step". A drop or a ramp needs one sensor, as for the core. A
    drop, stairs down or an obstacle at stopping distance is URGENT; anything else on the way within
    the core's reaction distances is a WARNING. Once the user has said yes to stairs down
    (`core.down_ok`) the decision already says that MANA is going down, so no direction is given
    for them. The nearest of the most urgent hazards wins.
    """
    lim = core.limits

    def good(report) -> bool:
        return now - report.stamp <= lim.max_age_s and report.trust >= lim.min_trust

    found: list[tuple[Priority, float, str]] = []  # priority, distance, sentence
    step = core._confirmed_step()  # two sensors agree, and the distance is known
    reports = [
        r for r in core.ground.values() if good(r) and r.ground in {Ground.DROP, Ground.RAMP}
    ]
    if step is not None:
        reports.append(step)
    for r in reports:
        near = not r.distance_m > lim.step_m  # an unknown distance counts as near
        if not near or r.ground not in STEPS | {Ground.DROP, Ground.RAMP}:
            continue
        known = not math.isnan(r.distance_m)
        where = f", {metres(r.distance_m)}" if known else ""
        at_edge = known and r.distance_m <= lim.edge_m
        if r.ground == Ground.DROP:
            found.append(
                (
                    Priority.URGENT if at_edge else Priority.WARNING,
                    r.distance_m,
                    f"Drop ahead{where}. Stop.",
                )
            )
        elif r.ground == Ground.STAIRS_DOWN:
            if getattr(core, "down_ok", False):
                continue
            found.append(
                (
                    Priority.URGENT if at_edge else Priority.WARNING,
                    r.distance_m,
                    f"Steps down ahead{where}. Stop.",
                )
            )
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
