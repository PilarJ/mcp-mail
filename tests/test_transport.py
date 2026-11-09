"""Do the IMAP and SMTP clients verify the certificate and hostname?

Real TLS over loopback against a thread that records what the client sends after the
handshake. No fakes: the point is what the actual imaplib and smtplib do with the
context the server gives them. Credentials reaching the server is the failure.
"""

from __future__ import annotations

import datetime
import socket
import ssl
import threading
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from mail_mcp import smtp
from mail_mcp.imap import ImapClient, MailError

from .support import make_settings

SECRET = "transport-test-password"


def write_certificate(directory: Path, hostname: str) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / f"{hostname}.crt"
    key_path = directory / f"{hostname}.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


class TlsServer:
    """Speaks just enough IMAP or SMTP to get a client to reveal its login."""

    def __init__(self, cert: Path, key: Path, protocol: str, starttls: bool = False) -> None:
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert, key)
        self.protocol = protocol
        self.starttls = starttls
        self.received = bytearray()
        self.handshakes = 0
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        self.listener.settimeout(5)
        try:
            raw, _ = self.listener.accept()
        except OSError:
            return
        raw.settimeout(5)
        try:
            if self.starttls:
                raw.sendall(b"220 test ESMTP\r\n")
                file = raw.makefile("rb")
                while True:
                    line = file.readline()
                    if not line:
                        return
                    if line.upper().startswith(b"EHLO"):
                        raw.sendall(b"250-test\r\n250 STARTTLS\r\n")
                    elif line.upper().startswith(b"STARTTLS"):
                        raw.sendall(b"220 go ahead\r\n")
                        break
            connection = self.context.wrap_socket(raw, server_side=True)
            self.handshakes += 1
            self._talk(connection)
        except (OSError, ssl.SSLError):
            return

    def _talk(self, connection: ssl.SSLSocket) -> None:
        if self.protocol == "imap":
            connection.sendall(b"* OK [CAPABILITY IMAP4rev1] ready\r\n")
        elif not self.starttls:
            connection.sendall(b"220 test ESMTP\r\n")
        while True:
            data = connection.recv(4096)
            if not data:
                return
            self.received.extend(data)
            for line in data.splitlines():
                upper = line.upper()
                tag = line.split(b" ", 1)[0]
                if self.protocol == "imap":
                    if b"CAPABILITY" in upper:
                        connection.sendall(b"* CAPABILITY IMAP4rev1\r\n" + tag + b" OK done\r\n")
                    else:
                        connection.sendall(tag + b" NO refused\r\n")
                elif upper.startswith(b"EHLO"):
                    connection.sendall(b"250-test\r\n250 AUTH PLAIN LOGIN\r\n")
                else:
                    connection.sendall(b"535 refused\r\n")

    def close(self) -> None:
        self.listener.close()


@pytest.fixture
def certificates(tmp_path):
    return {name: write_certificate(tmp_path, name) for name in ("localhost", "other.example.com")}


def imap_login(settings) -> None:
    ImapClient(settings)._run(lambda mailbox: None)


def smtp_send(settings) -> None:
    message = smtp.build_message(settings, ["client@example.net"], "s", "b")
    smtp._send_sync(settings, message, ["client@example.net"])


def settings_for(tmp_path, port: int, smtp_port: int | None = None):
    return make_settings(
        tmp_path,
        IMAP_HOST="localhost",
        IMAP_PORT=str(port),
        SMTP_HOST="localhost",
        SMTP_PORT=str(smtp_port if smtp_port is not None else port),
        IMAP_PASS=SECRET,
        SMTP_PASS=SECRET,
    )


def assert_no_credentials(server: TlsServer) -> None:
    server.close()
    assert SECRET.encode() not in bytes(server.received)
    assert b"LOGIN" not in bytes(server.received).upper()
    assert b"AUTH" not in bytes(server.received).upper()


def test_imap_refuses_an_untrusted_certificate(tmp_path, certificates, monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    server = TlsServer(*certificates["localhost"], "imap")
    with pytest.raises(MailError):
        imap_login(settings_for(tmp_path, server.port))
    assert_no_credentials(server)


def test_imap_refuses_a_certificate_for_another_hostname(tmp_path, certificates, monkeypatch):
    # trusted by the client, but issued for a different name
    monkeypatch.setenv("SSL_CERT_FILE", str(certificates["other.example.com"][0]))
    server = TlsServer(*certificates["other.example.com"], "imap")
    with pytest.raises(MailError):
        imap_login(settings_for(tmp_path, server.port))
    assert_no_credentials(server)


def test_imap_accepts_a_trusted_certificate_for_the_right_hostname(tmp_path, certificates, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(certificates["localhost"][0]))
    server = TlsServer(*certificates["localhost"], "imap")
    with pytest.raises(MailError):  # the test server refuses the login itself
        imap_login(settings_for(tmp_path, server.port))
    server.close()
    assert server.handshakes == 1
    assert b"LOGIN" in bytes(server.received).upper()


def test_smtp_starttls_refuses_an_untrusted_certificate(tmp_path, certificates, monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    server = TlsServer(*certificates["localhost"], "smtp", starttls=True)
    with pytest.raises(ssl.SSLError):
        smtp_send(settings_for(tmp_path, server.port))
    assert_no_credentials(server)


def test_smtp_starttls_refuses_a_certificate_for_another_hostname(tmp_path, certificates, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(certificates["other.example.com"][0]))
    server = TlsServer(*certificates["other.example.com"], "smtp", starttls=True)
    with pytest.raises(ssl.SSLError):
        smtp_send(settings_for(tmp_path, server.port))
    assert_no_credentials(server)


def test_smtp_starttls_accepts_a_trusted_certificate(tmp_path, certificates, monkeypatch):
    import smtplib

    monkeypatch.setenv("SSL_CERT_FILE", str(certificates["localhost"][0]))
    server = TlsServer(*certificates["localhost"], "smtp", starttls=True)
    with pytest.raises(smtplib.SMTPException):  # the test server refuses the login itself
        smtp_send(settings_for(tmp_path, server.port))
    server.close()
    assert server.handshakes == 1


def test_smtp_implicit_tls_refuses_an_untrusted_certificate(tmp_path, certificates, monkeypatch):
    """Port 465 path. The real port cannot be bound, so map 465 onto the test port."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    server = TlsServer(*certificates["localhost"], "smtp")
    real_ssl = smtp.smtplib.SMTP_SSL

    def to_test_port(host, _port, **kwargs):
        return real_ssl(host, server.port, **kwargs)

    monkeypatch.setattr(smtp.smtplib, "SMTP_SSL", to_test_port)
    with pytest.raises(ssl.SSLError):
        smtp_send(settings_for(tmp_path, server.port, smtp_port=465))
    assert_no_credentials(server)


def test_certificate_helper_has_verification_on():
    from mail_mcp.tls import client_context

    context = client_context()
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
