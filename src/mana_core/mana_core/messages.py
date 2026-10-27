"""What the user hears: one message at a time, the most urgent first.

The decision core makes a message every time its reason changes. Said as they come, an old
"Level ground" could be spoken before a new "Drop ahead, stopping", and two warnings could talk over
each other. The queue fixes that: it lets one message out at a time, urgent ones first, and it drops
what is no longer true. Pure Python, no ROS.
"""

import math
from dataclasses import dataclass

from mana_core.decision import Priority

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
      same direction as the last one, is dropped. An URGENT direction is also dropped when an
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
