"""One scenario per fault: when to switch it on and off during a run, and what the core should do.

    ros2 run mana_sim fault_script gps_loss        # while the simulation and the core run

The times are simulation seconds from the start of the script. The pass marks come with the
scenario runner (the ten scenarios in Gazebo); here each scenario says in words what to expect.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    at_s: float
    sensor: str
    kind: str
    active: bool
    value: float = 0.0  # 0: the default strength of that fault


@dataclass(frozen=True)
class FaultScenario:
    name: str
    summary: str
    expect: str
    steps: tuple[Step, ...]


def on_off(sensor: str, kind: str, value: float = 0.0, on_s: float = 10.0, off_s: float = 30.0):
    return (Step(on_s, sensor, kind, True, value), Step(off_s, sensor, kind, False))


FAULT_SCENARIOS = {
    s.name: s
    for s in (
        FaultScenario(
            "depth_noise",
            "The 3D camera is noisy.",
            "Ground reports get less certain; MANA does not call a flat floor a step.",
            on_off("depth", "noise", 4.0),
        ),
        FaultScenario(
            "lidar_dropout",
            "The lidar sends nothing.",
            "No obstacle reports; MANA goes on with the other sources.",
            on_off("lidar", "dropout"),
        ),
        FaultScenario(
            "gps_loss",
            "The GPS is lost.",
            "No position reports; the position source switches (the position work).",
            on_off("gps", "dropout"),
        ),
        FaultScenario(
            "wifi_fade",
            "The Wi-Fi link fades to nothing in 10 s.",
            "The Wi-Fi link is reported gone and the alerts change to Bluetooth. MANA does not "
            "slow down: Bluetooth still carries them.",
            on_off("wifi", "fade", 10.0),
        ),
        FaultScenario(
            "wheel_slip",
            "The encoders say the wheels turn twice as fast as the chair moves.",
            "A slip report; on wheels MANA changes to tracks.",
            on_off("wheels", "slip", 2.0),
        ),
        FaultScenario(
            "darkness",
            "The camera keeps 5 percent of the light.",
            "The camera's reports are distrusted; the 3D camera carries on.",
            on_off("camera", "dark"),
        ),
        FaultScenario(
            "imu_lost",
            "The IMU sends nothing.",
            "No motion reports; MANA does not rely on tilt.",
            on_off("imu", "dropout"),
        ),
    )
}


def due(steps: tuple[Step, ...], elapsed_s: float, done: int) -> list[Step]:
    """The steps whose time has come and that have not been done yet. done is how many were sent;
    the steps are in order of time."""
    out = []
    for step in steps[done:]:
        if step.at_s > elapsed_s:
            break
        out.append(step)
    return out
