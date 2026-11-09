"""Connect an Android phone to the demo over a USB cable, so no Wi-Fi is needed, or over Wi-Fi with
Android's wireless debugging (Android 11 and later), so no cable is needed.

    python -m demo.usb              # then open http://localhost:8000/phone on the phone
    python -m demo.usb --pair 192.168.1.23:37011 482915 --wifi 192.168.1.23:41235   # first time
    python -m demo.usb --wifi 192.168.1.23:41235                                    # after that

It runs `adb reverse`, which makes the phone's own localhost:PORT reach this laptop through the
cable. A page on localhost counts as a secure page, so the phone gives it the camera, motion,
location and microphone with no certificate. The phone's Wi-Fi and Bluetooth can then be switched
off to show the alert link changing, without the demo losing its data.

Over Wi-Fi the same tunnel runs through adb's wireless connection, so the page is still on
localhost and keeps the camera and the sensors. The phone and the laptop must share a network (the
laptop's own hotspot will do, with no internet). The phone's Wi-Fi then carries the demo and cannot
be switched off to show the alert link changing: use the cable for that.

Needs the Android platform tools (adb) and USB debugging (or wireless debugging) turned on, on the
phone.
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


def connect(
    port: int = 8000,
    adb: str | None = None,
    run=run_adb,
    wifi: str | None = None,
    pair: tuple[str, str] | None = None,
) -> tuple[bool, str]:
    """Set up the tunnel, over the cable or, with wifi (the phone's IP:PORT from Wireless
    debugging), over Wi-Fi; pair is (IP:PORT, code) from "Pair device with pairing code", needed
    once per laptop. Returns (ok, a message for the user)."""
    adb = adb or find_adb()
    if adb is None:
        return False, (
            "adb was not found. Install the Android platform tools and put adb on the PATH: "
            "the cable and the Wi-Fi way both need it."
        )
    if pair is not None:
        code, out = run(adb, "pair", *pair)
        if code != 0 or "Successfully paired" not in out:
            return False, f"Pairing failed: {out}. Check the pairing address and code on the phone."
    if wifi is not None:
        code, out = run(adb, "connect", wifi)
        if code != 0 or "connected to" not in out:
            return False, (
                f"adb could not reach the phone at {wifi}: {out}. Check that both are on the same "
                "network and that the address is the one under Wireless debugging (pair first)."
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
    phone = wifi if wifi is not None else next((d for d in ready if ":" not in d), ready[0])
    if phone not in ready:
        return False, f"The phone at {phone} is not ready: {out}"
    code, out = run(adb, "-s", phone, "reverse", f"tcp:{port}", f"tcp:{port}")
    if code != 0:
        return False, f"adb reverse failed: {out}"
    how = "over Wi-Fi" if wifi is not None else "by cable"
    return (
        True,
        f"Phone {phone} is connected {how}. On the phone open http://localhost:{port}/phone",
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Connect an Android phone to the demo, by USB or Wi-Fi")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--wifi", metavar="IP:PORT", help="the address under Wireless debugging")
    p.add_argument("--pair", nargs=2, metavar=("IP:PORT", "CODE"), help="pair first (once)")
    a = p.parse_args(argv)
    ok, message = connect(a.port, wifi=a.wifi, pair=tuple(a.pair) if a.pair else None)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
