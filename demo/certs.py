"""A certificate of the laptop's own, so the phone can open the demo over the network as an https
page, which Chrome needs before it gives a page the camera, the motion sensors and GPS.

    python -m demo.server --https      # then on the phone: https://<laptop address>:8000/phone

The certificate is made once and kept in certs/ (not in git), for localhost and every address the
laptop has on its networks. It is signed by itself, not by an authority, so the phone warns once:
tap Advanced, then Proceed. It is made again when the laptop has an address it does not name, which
makes the phone warn again.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import os
import socket
import ssl
from pathlib import Path

FOLDER = Path(__file__).resolve().parent.parent / "certs"  # git-ignored, wherever it is run


def addresses() -> list[str]:
    """The laptop's IPv4 addresses other than loopback: where the phone can reach it. The address
    of the route out (the Wi-Fi, usually) comes first; the others can be virtual adapters (WSL,
    VirtualBox) that a phone cannot reach."""
    found, route = set(), None
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:  # the address of the route out, which getaddrinfo can miss; nothing is sent
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            route = s.getsockname()[0]
    except OSError:
        pass
    others = sorted(a for a in found - {route} if not a.startswith("127."))
    return ([route] if route and not route.startswith("127.") else []) + others


def make(cert: Path, key: Path, ips: list[str]) -> None:
    """A self-signed certificate for localhost and these addresses, valid for a year."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    private = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MANA demo")])
    alt = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    alt += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .sign(private, hashes.SHA256())
    )
    cert.parent.mkdir(parents=True, exist_ok=True)
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    # unencrypted, as the server starts with no one to type a password: readable by the owner only
    with os.fdopen(os.open(key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        f.write(pem)
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def names(cert: Path) -> set[str]:
    """The addresses a certificate on disk is for; empty when there is none or it has expired."""
    from cryptography import x509

    try:
        c = x509.load_pem_x509_certificate(cert.read_bytes())
    except (OSError, ValueError):
        return set()
    if c.not_valid_after_utc <= dt.datetime.now(dt.UTC):
        return set()
    alt = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return {str(ip) for ip in alt.get_values_for_type(x509.IPAddress)}


def context(folder: Path | str = FOLDER, ips: list[str] | None = None) -> ssl.SSLContext:
    """A server context with the kept certificate, made again if it does not name every address."""
    folder = Path(folder)
    cert, key = folder / "demo-cert.pem", folder / "demo-key.pem"
    ips = addresses() if ips is None else ips
    if not set(ips) <= names(cert) or not key.exists():
        make(cert, key, ips)
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(cert, key)
    return ctx
