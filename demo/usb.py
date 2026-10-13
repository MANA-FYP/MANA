"""Connect an Android phone to the demo over a USB cable, so no Wi-Fi is needed.

    python -m demo.usb              # then open http://localhost:8000/phone on the phone

It runs `adb reverse`, which makes the phone's own localhost:PORT reach this laptop through the
cable. A page on localhost counts as a secure page, so the phone gives it the camera, motion,
location and microphone with no certificate. The phone's Wi-Fi and Bluetooth can then be switched
off to show the alert link changing, without the demo losing its data.

Needs the Android platform tools (adb) and USB debugging turned on, on the phone.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess


def find_adb() -> str | None:
    return shutil.which("adb")


def run_adb(adb: str, *args: str) -> tuple[int, str]:
    done = subprocess.run([adb, *args], capture_output=True, text=True, timeout=20)
    return done.returncode, (done.stdout + done.stderr).strip()


def parse_devices(text: str) -> tuple[list[str], list[str]]:
    """(ready, not ready) serials from the output of `adb devices`."""
    ready, waiting = [], []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            (ready if parts[1] == "device" else waiting).append(parts[0])
    return ready, waiting


def connect(port: int = 8000, adb: str | None = None, run=run_adb) -> tuple[bool, str]:
    """Set up the cable. Returns (ok, a message for the user)."""
    adb = adb or find_adb()
    if adb is None:
        return False, (
            "adb was not found. Install the Android platform tools and put adb on the PATH, "
            "or use the Wi-Fi way in demo/README.md."
        )
    code, out = run(adb, "devices")
    if code != 0:
        return False, f"adb could not list devices: {out}"
    ready, waiting = parse_devices(out)
    if not ready:
        if waiting:
            return False, (
                "The phone is connected but not ready. Unlock it and accept the "
                "'Allow USB debugging' question."
            )
        return False, "No phone found. Plug it in and turn on USB debugging in Developer options."
    code, out = run(adb, "reverse", f"tcp:{port}", f"tcp:{port}")
    if code != 0:
        return False, f"adb reverse failed: {out}"
    return True, f"Phone {ready[0]} is connected. On the phone open http://localhost:{port}/phone"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Connect an Android phone to the demo over USB")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args(argv)
    ok, message = connect(a.port)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
