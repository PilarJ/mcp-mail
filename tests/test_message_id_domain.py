"""The Message-ID takes its domain from the sender, never from the host it runs on (#18)."""

from __future__ import annotations

import socket

import pytest

from mail_mcp import smtp

from .support import make_settings

FALLBACK = "localhost"


@pytest.fixture(autouse=True)
def leaky_host(monkeypatch):
    # A host name that must never reach a header, whatever the sender looks like.
    monkeypatch.setattr(socket, "getfqdn", lambda *a, **k: "internal-container.corp.invalid")


def message_id(tmp_path, **overrides: str) -> str:
    settings = make_settings(tmp_path, **overrides)
    return smtp.build_message(settings, ["client@example.net"], "s", "b")["Message-ID"]


def test_message_id_uses_sender_domain(tmp_path):
    mid = message_id(tmp_path)
    assert mid.startswith("<") and mid.endswith("@example.org>")
    assert "internal-container" not in mid


def test_domain_is_lowercased(tmp_path):
    assert message_id(tmp_path, SMTP_USER="Info@Example.ORG").endswith("@example.org>")


def test_display_name_form_is_understood(tmp_path):
    mid = message_id(tmp_path, SMTP_USER="Shop Team <team@shop.example.com>")
    assert mid.endswith("@shop.example.com>")


def test_idn_domain_is_punycoded(tmp_path):
    mid = message_id(tmp_path, SMTP_USER="info@příklad.example")
    assert mid.endswith("@xn--pklad-zsa96e.example>")
    assert mid.isascii()


@pytest.mark.parametrize(
    "user",
    [
        "plain-login",  # no domain at all
        "user@",  # empty domain
        "user@-bad-.example",  # not a valid host name
        "user@[192.0.2.1]",  # address literal
    ],
)
def test_unusable_domain_falls_back(tmp_path, user):
    mid = message_id(tmp_path, SMTP_USER=user)
    assert mid.endswith(f"@{FALLBACK}>")
    assert "internal-container" not in mid


def test_message_ids_stay_unique(tmp_path):
    settings = make_settings(tmp_path)
    ids = {smtp.build_message(settings, ["client@example.net"], "s", "b")["Message-ID"] for _ in range(50)}
    assert len(ids) == 50
