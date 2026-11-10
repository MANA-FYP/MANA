"""Faults that come with a place (#79): GPS is lost indoors, the dark room is dark, Wi-Fi fades in
the hall. Pure Python, no ROS: zone_node.py follows the chair and switches the faults of the zone
it enters through fault_node's set_fault, as fault_script does on a timetable.

A zone is a rectangle (x0, x1, y0, y1) of the world, the campus's by default (mana_sim.campus).
The chair is in it once its centre is inside, and leaves it only once it is `margin_m` outside, so
standing in a doorway does not switch the fault on and off.
"""

from dataclasses import dataclass, field

# what each zone switches: (sensor, kind, value); the kinds and values of mana_sim.faults
ZONE_FAULTS = {
    "indoors": (("gps", "dropout", 1.0),),
    "dark_room": (("camera", "dark", 0.05),),
    "weak_wifi": (("wifi", "fade", 10.0),),
}


@dataclass
class ZoneWatch:
    zones: dict[str, tuple[float, float, float, float]]
    faults: dict[str, tuple[tuple[str, str, float], ...]] = field(
        default_factory=lambda: dict(ZONE_FAULTS)
    )
    margin_m: float = 0.3
    inside: set[str] = field(default_factory=set)

    def _in(self, name: str, x: float, y: float) -> bool:
        x0, x1, y0, y1 = self.zones[name]
        m = self.margin_m if name in self.inside else 0.0
        return x0 - m <= x <= x1 + m and y0 - m <= y <= y1 + m

    def update(self, x: float, y: float) -> list[tuple[str, str, bool, float, str]]:
        """The faults to switch now: (sensor, kind, on, value, zone), on entering or leaving."""
        changes = []
        for name in self.zones:
            if name not in self.faults:
                continue
            now_in = self._in(name, x, y)
            if now_in == (name in self.inside):
                continue
            (self.inside.add if now_in else self.inside.discard)(name)
            changes += [(s, k, now_in, v, name) for s, k, v in self.faults[name]]
        return changes
