"""The caregiver's status view (M5 interface): what a helper needs to know, and nothing to press.

The caregiver is a secondary user (UC-9, FR-23). They follow the user from their own phone and
see whether MANA is moving or stopped, on wheels or tracks, where its position comes from, which
radio carries alerts, and the last alerts. The view is built from the dashboard's snapshot, so it
shows the same state, but it leaves out the camera image and the raw sensor stream: a helper does
not need to watch through the user's camera.

The server sends this view on /ws/caregiver and /api/status. Neither accepts any command.
"""

from __future__ import annotations

ALERTS = 8  # how many of the latest alerts to show
STALE_S = 3.0  # the phone has sent nothing for this long: MANA is offline for the caregiver

MODE_TEXT = {"WHEELS": "On wheels", "TRACKS": "On tracks"}
POSITION_TEXT = {"gps": "GPS", "indoor": "Indoor map"}
# Sentences written to the user, as the caregiver should read them.
FOR_CAREGIVER = {
    "MANA deactivated. You are fully in control.": "Stopped by the user. They are in control.",
    "Manual mode. You are in control of the device.": "Manual mode. The user is driving.",
    "Stopped by you": "Stopped by the user",
}
LINK_TEXT = {
    "wifi": "Wi-Fi",
    "bluetooth": "Bluetooth",
    "none": "No link",
    "unknown": "Unknown",
}


def for_caregiver(text: str) -> str:
    """A sentence meant for the user, reworded for someone else reading it."""
    for said, meant in FOR_CAREGIVER.items():
        text = text.replace(said, meant)
    return text


def state_of(decision: dict) -> str:
    """One word for how MANA is doing: stopped, slow (careful) or moving."""
    if decision.get("stop"):
        return "stopped"
    if decision.get("speed", 1.0) < 1.0:
        return "slow"
    return "moving"


def view(snap: dict, phone_connected: bool, phone_age_s: float | None = None) -> dict:
    """The caregiver's view of one dashboard snapshot.

    phone_age_s is how long ago the user's phone last sent anything; None when it never has.
    """
    d = snap.get("decision", {})
    position = snap.get("position", {})
    link = snap.get("link", {})
    online = phone_connected and phone_age_s is not None and phone_age_s <= STALE_S
    accuracy = position.get("accuracy_m")
    alerts = [
        {"t": a["t"], "kind": a["kind"], "text": for_caregiver(a["text"])}
        for a in reversed(snap.get("decisions", []))
        if a.get("kind") != "detection"  # what the camera sees is not an alert
    ][:ALERTS]
    return {
        "type": "caregiver",
        "time": snap.get("time", 0.0),
        "online": online,
        "state": state_of(d) if online else "offline",
        "status": for_caregiver(d.get("sentence") or d.get("reason", "")),
        "priority": d.get("priority", "INFO"),
        "mode": MODE_TEXT.get(d.get("mode", ""), d.get("mode", "")),
        "manual": bool(snap.get("manual", False)),
        "speed_percent": round(100 * d.get("speed", 0.0)),
        "position": {
            "source": POSITION_TEXT.get(position.get("source", ""), "Unknown"),
            "accuracy_m": None if accuracy is None else round(accuracy, 1),
        },
        "link": {
            "state": link.get("state", "unknown"),
            "text": LINK_TEXT.get(link.get("state", "unknown"), "Unknown"),
            "rtt_ms": link.get("rtt_ms"),
        },
        "alerts": alerts,
    }
