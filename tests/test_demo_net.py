"""The phone over the network: the demo's own https certificate, and the tunnel's address."""

import ssl

import pytest

from demo import tunnel

pytest.importorskip("cryptography")  # demo/requirements.txt; certs makes the certificate with it
from demo import certs  # noqa: E402


def test_the_certificate_names_the_laptops_addresses_and_is_kept(tmp_path):
    ctx = certs.context(tmp_path, ips=["192.168.1.2"])
    assert isinstance(ctx, ssl.SSLContext)
    cert = tmp_path / "demo-cert.pem"
    assert certs.names(cert) == {"127.0.0.1", "192.168.1.2"}
    made = cert.read_bytes()
    certs.context(tmp_path, ips=["192.168.1.2"])
    assert cert.read_bytes() == made  # kept: the phone does not warn again
    certs.context(tmp_path, ips=["10.0.0.7"])
    assert "10.0.0.7" in certs.names(cert)  # a new address: made again


def test_no_certificate_names_nothing(tmp_path):
    assert certs.names(tmp_path / "none.pem") == set()


def test_the_tunnels_address_is_found_in_its_log():
    line = "2026-11-09T12:00:00Z INF |  https://quiet-river-maple-lamp.trycloudflare.com  |"
    assert tunnel.find_url(line) == "https://quiet-river-maple-lamp.trycloudflare.com"
    assert tunnel.find_url("INF Starting tunnel") is None
    assert (
        tunnel.find_url("INF Requesting new quick Tunnel on https://api.trycloudflare.com") is None
    )
    assert tunnel.command("cloudflared", 8000)[-2:] == ["--url", "http://localhost:8000"]


def test_a_tunnel_that_stops_with_no_address_says_what_cloudflared_said_last(monkeypatch, capsys):
    import sys

    from demo import access

    say = "import sys; sys.stderr.write('INF Starting\\nfailed to dial edge\\n'); sys.exit(1)"
    monkeypatch.setattr(access, "read", lambda: "k")
    monkeypatch.setattr(tunnel, "command", lambda exe, port: [sys.executable, "-c", say])
    assert tunnel.main(["--cloudflared", "x"]) == 1
    out = capsys.readouterr().out
    assert "stopped (exit 1) before giving an address" in out and "failed to dial edge" in out
