"""The demo on a public https address, so a phone anywhere with internet can open it: no cable, no
shared Wi-Fi, no certificate warning. A Cloudflare quick tunnel (no account) carries it.

    python -m demo.server                 # as usual, on this laptop
    python -m demo.tunnel                 # prints https://<random words>.trycloudflare.com/phone

Needs Cloudflare's `cloudflared` on the PATH (or --cloudflared PATH), installed by you from
Cloudflare's own release page. Every message goes through Cloudflare, so the round trip is longer
than on the same Wi-Fi (`demo.server --https`); the dashboard shows it. Anyone with the address can
open the dashboard and see the camera, so the address is for this session only: stop the tunnel
(Ctrl+C) when the demo ends.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys

URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


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
    proc = subprocess.Popen(command(a.cloudflared, a.port), stderr=subprocess.PIPE, text=True)
    try:
        for line in proc.stderr:
            url = find_url(line)
            if url:
                print(f"phone:     {url}/phone")
                print(f"dashboard: {url}/dashboard   (or http://localhost:{a.port}/dashboard)")
                print("Ctrl+C ends the tunnel.", flush=True)
                break
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    sys.exit(main())
