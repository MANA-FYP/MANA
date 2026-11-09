"""The demo on a public https address, so a phone anywhere with internet can open it: no cable, no
shared Wi-Fi, no certificate warning. A Cloudflare quick tunnel (no account) carries it.

    python -m demo.server                 # as usual, on this laptop
    python -m demo.tunnel                 # prints https://<random words>.trycloudflare.com/phone

Needs Cloudflare's `cloudflared` on the PATH (or --cloudflared PATH), installed by you from
Cloudflare's own release page. Every message goes through Cloudflare, so the round trip is longer
than on the same Wi-Fi (`demo.server --https`); the dashboard shows it.

The server answers only requests with its key (demo.access), and the addresses printed here carry
it. Anyone with such an address can drive the device, and see and hear what the phone sends: share
it with no one, and stop the tunnel (Ctrl+C) when the demo ends. The tunnel does not start before
the server has made its key.
"""

from __future__ import annotations

import argparse
import collections
import re
import shutil
import subprocess
import sys

from demo import access

# the tunnel's own address: cloudflared also logs api.trycloudflare.com, where it asks for one
URL = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")


def command(cloudflared: str, port: int) -> list[str]:
    return [cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"]


def find_url(line: str) -> str | None:
    """The tunnel's public address, in one line of cloudflared's log."""
    m = URL.search(line)
    return m.group(0) if m else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", type=int, default=8000, help="the demo server's port")
    p.add_argument("--cloudflared", default=shutil.which("cloudflared"), help="its path")
    a = p.parse_args(argv)
    if not a.cloudflared:
        print("cloudflared was not found: install it from Cloudflare, or give --cloudflared PATH")
        return 1
    key = access.read()
    if key is None:
        print("The server has no key yet: start python -m demo.server first, then the tunnel.")
        return 1
    proc = subprocess.Popen(command(a.cloudflared, a.port), stderr=subprocess.PIPE, text=True)
    found = False
    last = collections.deque(maxlen=8)  # what it said last, for when it stops with no address
    try:
        for line in proc.stderr:  # read to the end: a full pipe would stall cloudflared
            last.append(line.rstrip())
            url = None if found else find_url(line)
            if url:
                found = True
                print(f"live:      {access.with_key(url + '/live', key)}")
                print(f"phone:     {access.with_key(url + '/phone', key)}")
                print(f"dashboard: {access.with_key(url + '/dashboard', key)}")
                print("Anyone with these addresses can drive the device and see the camera:")
                print("share them with no one. Ctrl+C ends the tunnel.", flush=True)
            elif " ERR " in line:
                print(line.rstrip(), flush=True)
        code = proc.wait()
        if not found:
            print(f"cloudflared stopped (exit {code}) before giving an address. It said last:")
            print("\n".join(last) or "(nothing)", flush=True)
        return code
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    sys.exit(main())
