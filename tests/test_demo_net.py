"""The phone over the network: the demo's own https certificate, and the tunnel's address."""

import ssl

from demo import certs, tunnel


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
    assert tunnel.command("cloudflared", 8000)[-2:] == ["--url", "http://localhost:8000"]
