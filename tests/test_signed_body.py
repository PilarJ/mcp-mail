"""The approval is read from the octets the signature covers, not through unsigned headers.

Real approver mail signs From, Subject, Date, To, References and In-Reply-To, and no MIME
header at all. So Content-Type and Content-Transfer-Encoding can be added or changed without
breaking the signature, and they must not be able to change what the signed body says. Each
attack here starts from a genuinely signed message that approves nothing, and the signature
still verifies after the change.
"""

from __future__ import annotations

import base64

import dkim
import pytest

from .support import APPROVER, MAILBOX, _crlf, dkim_sign, iphone_reply, plain_message

pytestmark = pytest.mark.anyio

ORDINARY = "Běžná zpráva, žádný souhlas.\n"


def head(content_type: str, *extra: str) -> str:
    lines = [
        f"From: Test Approver <{APPROVER}>",
        f"To: {MAILBOX}",
        "Subject: Re: Ke schválení",
        "Date: Mon, 9 Nov 2026 10:00:00 +0100",
        "References: <request@example.org>",
        "In-Reply-To: <request@example.org>",
        "MIME-Version: 1.0",
        f"Content-Type: {content_type}",
        *extra,
    ]
    return "\n".join(lines) + "\n\n"


async def assert_signed_and_refused(harness, pending, raw: bytes) -> dict:
    # The whole point: dkimpy is satisfied, so only the reading of the body can refuse this.
    assert dkim.DKIM(raw).verify(dnsfunc=harness.dns_lookup) is True
    harness.dns_queries.clear()
    harness.deliver(raw)
    result = await harness.send(pending)
    assert result["status"] == "refused", result
    assert harness.delivered() == []
    assert "approved" not in harness.log_kinds()
    assert pending.code not in str(result)
    return result


def swapped(signed: bytes, old: bytes, new: bytes) -> bytes:
    """Change one unsigned header, which leaves the signature valid."""
    assert signed.count(old) == 1
    return signed.replace(old, new)


# ---------------------------------------------------------------- a break the signature cannot see


async def test_bare_cr_in_an_added_header_hides_a_new_body(harness, keys):
    pending = await harness.request()
    signed = dkim_sign(plain_message(ORDINARY), keys["approver"])
    phrase = f"souhlasim {pending.code}".encode()
    await assert_signed_and_refused(harness, pending, b"X-Note: x\r\r" + phrase + b"\r\n" + signed)


async def test_bare_cr_after_the_last_header_hides_a_new_body(harness, keys):
    pending = await harness.request()
    signed = dkim_sign(plain_message(ORDINARY), keys["approver"])
    last_header = b"Content-Transfer-Encoding: 8bit\r\n"
    phrase = f"souhlasím {pending.code}".encode()
    tampered = swapped(signed, last_header, last_header + b"X-Note: x\r\r" + phrase + b"\r\n")
    await assert_signed_and_refused(harness, pending, tampered)


@pytest.mark.parametrize(
    "stray", [pytest.param(b"\r", id="bare-cr"), pytest.param(b"\n", id="bare-lf")]
)
async def test_stray_line_ending_inside_a_signed_approval_refuses(harness, keys, stray):
    pending = await harness.request()
    text = f"souhlasím {pending.code}\r\n".encode() + b"dalsi" + stray + b"radek\r\n"
    raw = _crlf(head('text/plain; charset="utf-8"', "Content-Transfer-Encoding: 8bit")).rstrip(b"\r\n") + b"\r\n\r\n" + text
    result = await assert_signed_and_refused(harness, pending, dkim_sign(raw, keys["approver"]))
    assert "line ending" in result["detail"]


# ---------------------------------------------------------------- transfer encoding


def encoded_phrase(code: str) -> str:
    return base64.b64encode(f"souhlasím {code}\n".encode()).decode() + "\n"


async def test_changing_the_transfer_encoding_to_base64(harness, keys):
    pending = await harness.request()
    signed = dkim_sign(plain_message(encoded_phrase(pending.code)), keys["approver"])
    tampered = swapped(signed, b"Content-Transfer-Encoding: 8bit", b"Content-Transfer-Encoding: base64")
    await assert_signed_and_refused(harness, pending, tampered)


async def test_adding_a_transfer_encoding_that_was_absent(harness, keys):
    pending = await harness.request()
    raw = _crlf(head('text/plain; charset="utf-8"') + encoded_phrase(pending.code))
    signed = dkim_sign(raw, keys["approver"])
    await assert_signed_and_refused(harness, pending, b"Content-Transfer-Encoding: base64\r\n" + signed)


@pytest.mark.parametrize(
    "added",
    [
        pytest.param(b"Content-Transfer-Encoding: base64\r\n", id="transfer-encoding"),
        pytest.param(b'Content-Type: text/plain; charset="utf-7"\r\n', id="content-type"),
        pytest.param(b"MIME-Version: 1.0\r\n", id="mime-version"),
    ],
)
async def test_a_second_mime_header_refuses(harness, keys, added):
    pending = await harness.request()
    signed = dkim_sign(plain_message(f"souhlasím {pending.code}\n"), keys["approver"])
    await assert_signed_and_refused(harness, pending, added + signed)


async def test_mime_header_with_space_before_the_colon_refuses(harness, keys):
    """dkimpy cannot parse this one at all, so there is no signature check to get past."""
    pending = await harness.request()
    signed = dkim_sign(plain_message(encoded_phrase(pending.code)), keys["approver"])
    harness.deliver(b"Content-Transfer-Encoding : base64\r\n" + signed)
    result = await harness.send(pending)
    assert result["status"] == "refused", result
    assert harness.delivered() == []


async def test_declared_base64_over_a_written_phrase_refuses(harness, keys):
    """The header and the octets disagree. Readers would differ, so nobody gets to choose."""
    pending = await harness.request()
    signed = dkim_sign(plain_message(f"souhlasim {pending.code}\n"), keys["approver"])
    tampered = swapped(signed, b"Content-Transfer-Encoding: 8bit", b"Content-Transfer-Encoding: base64")
    await assert_signed_and_refused(harness, pending, tampered)


@pytest.mark.parametrize(
    "first_line",
    [
        pytest.param("=0Asouhlasim {code}", id="decoded-line-feed"),
        pytest.param("=0D=0Asouhlasim {code}", id="decoded-crlf"),
        pytest.param("=0Bsouhlasim {code}", id="decoded-vertical-tab"),
        pytest.param("=E2=80=A8souhlasim {code}", id="decoded-line-separator"),
    ],
)
async def test_quoted_printable_line_break_cannot_start_a_new_first_line(harness, keys, first_line):
    pending = await harness.request()
    text = first_line.format(code=pending.code)
    signed = dkim_sign(plain_message(f"{text}\n"), keys["approver"])
    tampered = swapped(signed, b"Content-Transfer-Encoding: 8bit", b"Content-Transfer-Encoding: quoted-printable")
    await assert_signed_and_refused(harness, pending, tampered)



# A first physical line ending in "=" is not the whole first line. In quoted-printable the
# "=" is a soft break and the line goes on below; in 8bit the "=" is part of the text. Either
# way, decoding that one physical line on its own would read an approval nobody wrote.
CONTINUED = [
    pytest.param("{phrase}=\n ale ne\n", id="soft-break"),
    pytest.param("{phrase}= \t\n ale ne\n", id="soft-break-with-trailing-space"),
    pytest.param("{phrase}=\n\n", id="soft-break-before-an-empty-line"),
]


@pytest.mark.parametrize("body", CONTINUED)
@pytest.mark.parametrize(
    "encoding",
    [
        pytest.param("Content-Transfer-Encoding: quoted-printable", id="quoted-printable"),
        pytest.param("Content-Transfer-Encoding: 8bit", id="8bit"),
        pytest.param(None, id="no-transfer-encoding"),
    ],
)
async def test_first_line_ending_in_an_equals_sign_refuses(harness, keys, encoding, body):
    pending = await harness.request()
    extra = (encoding,) if encoding else ()
    text = body.format(phrase=f"souhlasim {pending.code}")
    raw = _crlf(head('text/plain; charset="utf-8"', *extra) + text)
    await assert_signed_and_refused(harness, pending, dkim_sign(raw, keys["approver"]))


async def test_signed_8bit_line_with_an_equals_sign_relabelled_as_quoted_printable(harness, keys):
    """The approver wrote a literal "=". Calling the body quoted-printable must not drop it."""
    pending = await harness.request()
    signed = dkim_sign(plain_message(f"souhlasim {pending.code}=\nale ne\n"), keys["approver"])
    tampered = swapped(signed, b"Content-Transfer-Encoding: 8bit", b"Content-Transfer-Encoding: quoted-printable")
    await assert_signed_and_refused(harness, pending, tampered)


def test_a_truncated_quoted_printable_line_reads_as_no_approval():
    """The reviewer's helper-level check: one physical line ending in "=" is not a line."""
    from email import policy
    from email.parser import BytesParser

    from mail_mcp.approval import _single_part_line, approval_code_in

    for header in (b"quoted-printable", b"8bit"):
        declared = BytesParser(policy=policy.default).parsebytes(
            b"Content-Transfer-Encoding: " + header + b"\r\n\r\n"
        )
        extracted = _single_part_line(declared, b"souhlasim ABCDE-FGHJK=")
        assert approval_code_in(extracted or "") is None, header
        # RFC 2045 drops whitespace after a soft break, and so does a full MIME decoder.
        assert _single_part_line(declared, b"souhlasim ABCDE-FGHJK= \t") is None, header

async def test_malformed_content_type_over_a_written_phrase_refuses(harness, keys):
    pending = await harness.request()
    signed = dkim_sign(plain_message(f"souhlasim {pending.code}\n"), keys["approver"])
    tampered = swapped(signed, b'Content-Type: text/plain; charset="utf-8"', b"Content-Type: garbage")
    await assert_signed_and_refused(harness, pending, tampered)


async def test_part_with_invalid_base64_refuses_even_if_it_decodes_to_the_phrase(harness, keys):
    pending = await harness.request()
    encoded = encoded_phrase(pending.code)
    raw = _crlf(
        head('multipart/alternative; boundary="B"')
        + f"--B\nContent-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: base64\n\n!{encoded}--B--\n"
    )
    await assert_signed_and_refused(harness, pending, dkim_sign(raw, keys["approver"]))


async def test_transfer_encoding_on_a_multipart_refuses(harness, keys):
    pending = await harness.request()
    signed = dkim_sign(iphone_reply(f"souhlasím {pending.code}"), keys["approver"])
    await assert_signed_and_refused(harness, pending, b"Content-Transfer-Encoding: base64\r\n" + signed)


# ---------------------------------------------------------------- charset


async def test_changing_the_charset_to_utf7(harness, keys):
    pending = await harness.request()
    text = f"souhlas+AO0-m {pending.code}\n"
    signed = dkim_sign(plain_message(text), keys["approver"])
    tampered = swapped(signed, b'charset="utf-8"', b'charset="utf-7"')
    await assert_signed_and_refused(harness, pending, tampered)


# ---------------------------------------------------------------- structure


def fake_part(code: str, boundary: str = "X") -> str:
    return f"--{boundary}\nContent-Type: text/plain\n\nsouhlasim {code}\n--{boundary}--\n"


async def test_turning_a_signed_forward_into_a_multipart(harness, keys):
    """A forwarded text can hold what looks like a MIME part, written by a stranger."""
    pending = await harness.request()
    signed = dkim_sign(plain_message(f"Přeposílám.\n\n{fake_part(pending.code)}"), keys["approver"])
    tampered = swapped(
        signed, b'Content-Type: text/plain; charset="utf-8"', b'Content-Type: multipart/mixed; boundary="X"'
    )
    await assert_signed_and_refused(harness, pending, tampered)


async def test_selecting_another_boundary_in_a_signed_multipart(harness, keys):
    pending = await harness.request()
    raw = _crlf(
        head('multipart/alternative; boundary="B"')
        + '--B\nContent-Type: text/plain; charset="utf-8"\nContent-Transfer-Encoding: 8bit\n\n'
        + f"Přeposílám.\n\n{fake_part(pending.code)}\n--B--\n"
    )
    signed = dkim_sign(raw, keys["approver"])
    tampered = swapped(signed, b'boundary="B"', b'boundary="X"')
    await assert_signed_and_refused(harness, pending, tampered)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("Úvod před první částí.\n--B\nContent-Type: text/plain\n\nsouhlasim {code}\n--B--\n", id="preamble"),
        pytest.param(
            "Úvod před první částí.\n\nsouhlasim {code}\n--B\nContent-Type: text/plain\n\nObyčejný text.\n--B--\n",
            id="preamble-shaped-like-a-part",
        ),
        pytest.param("--B\nContent-Type: text/plain\n\nsouhlasim {code}\n", id="no-closing-delimiter"),
        pytest.param("--B\nContent-Type: text/plain\n\nsouhlasim {code}\n--Bx\nnoise\n--B--\n", id="look-alike-delimiter"),
        pytest.param("--B\nContent-Type: text/html\n\nsouhlasim {code}\n--B--\n", id="first-part-html"),
        pytest.param(
            "--B\nContent-Type: text/plain\nContent-Disposition: attachment; filename=a.txt\n\nsouhlasim {code}\n--B--\n",
            id="first-part-is-an-attachment",
        ),
        pytest.param(
            '--B\nContent-Type: multipart/alternative; boundary="C"\n\n'
            "--C\nContent-Type: text/plain\n\nsouhlasim {code}\n--C--\n--B--\n",
            id="nested-multipart",
        ),
        pytest.param(
            "--B\nContent-Type: text/plain\nContent-Type: text/html\n\nsouhlasim {code}\n--B--\n",
            id="two-content-types-in-the-part",
        ),
        pytest.param("--B\nno colon in this header\n\nsouhlasim {code}\n--B--\n", id="malformed-part-header"),
        pytest.param("--B\nContent-Type : text/plain\n\nsouhlasim {code}\n--B--\n", id="space-before-colon-in-part"),
        pytest.param(
            "--B\nContent-Type: text/plain\nContent-Transfer-Encoding: base64\n\n!!!not base64!!!\n--B--\n",
            id="broken-base64",
        ),
    ],
)
async def test_signed_multipart_that_cannot_be_read_one_way_refuses(harness, keys, body):
    pending = await harness.request()
    raw = _crlf(head('multipart/mixed; boundary="B"') + body.format(code=pending.code))
    await assert_signed_and_refused(harness, pending, dkim_sign(raw, keys["approver"]))


@pytest.mark.parametrize(
    "content_type",
    [
        pytest.param("multipart/digest; boundary=B", id="digest"),
        pytest.param("multipart/mixed", id="no-boundary"),
        pytest.param("garbage", id="malformed"),
        pytest.param("application/octet-stream", id="not-text"),
    ],
)
async def test_top_level_type_that_does_not_fit_refuses(harness, keys, content_type):
    pending = await harness.request()
    body = f"--B\nContent-Type: text/plain\n\nsouhlasim {pending.code}\n--B--\n"
    raw = _crlf(head(content_type) + body)
    await assert_signed_and_refused(harness, pending, dkim_sign(raw, keys["approver"]))


async def test_single_part_base64_refuses_because_its_encoding_is_unsigned(harness, keys):
    pending = await harness.request()
    raw = _crlf(
        head('text/plain; charset="utf-8"', "Content-Transfer-Encoding: base64") + encoded_phrase(pending.code)
    )
    await assert_signed_and_refused(harness, pending, dkim_sign(raw, keys["approver"]))


async def test_changing_a_signed_part_header_breaks_the_signature(harness, keys):
    pending = await harness.request()
    signed = dkim_sign(iphone_reply(f"souhlasím {pending.code}"), keys["approver"])
    tampered = signed.replace(b"Content-Transfer-Encoding: quoted-printable", b"Content-Transfer-Encoding: base64", 1)
    assert tampered != signed
    harness.deliver(tampered)
    result = await harness.send(pending)
    assert result["status"] == "refused"
    assert harness.delivered() == []


# ---------------------------------------------------------------- real mail still approves


def base64_part(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


GENUINE = [
    pytest.param(lambda code: plain_message(f"souhlasím {code}\n"), id="single-part-8bit"),
    pytest.param(
        lambda code: _crlf(head('text/plain; charset="utf-8"', "Content-Transfer-Encoding: quoted-printable")
                           + f"souhlas=C3=ADm {code}\n\nOdesl=C3=A1no z iPhonu\n"),
        id="single-part-quoted-printable",
    ),
    pytest.param(
        lambda code: _crlf(head('text/plain; charset="utf-8"', "Content-Transfer-Encoding: quoted-printable")
                           + f"souhlas=C3=ADm {code}\n\nDlouh=C3=BD podpis, kter=C3=BD klient zalomil=\n a pokra=C4=8Duje.\n"),
        id="quoted-printable-with-a-soft-break-below-the-first-line",
    ),
    pytest.param(
        lambda code: _crlf(head("text/plain") + f"souhlasim {code}\n"),
        id="single-part-no-transfer-encoding",
    ),
    pytest.param(lambda code: _crlf(head("text/plain") .replace("Content-Type: text/plain\n", "") + f"souhlasim {code}\n"), id="no-content-type"),
    pytest.param(lambda code: iphone_reply(f"souhlasím {code}"), id="iphone-alternative"),
    pytest.param(lambda code: iphone_reply(f"Souhlasím {code}"), id="iphone-capitalised"),
    pytest.param(
        lambda code: _crlf(
            head('multipart/alternative; boundary="000000abc"')
            + '--000000abc\nContent-Type: text/plain; charset="UTF-8"\nContent-Transfer-Encoding: base64\n\n'
            + base64_part(f"souhlasím {code}\n\n> citace\n")
            + "\n--000000abc\nContent-Type: text/html; charset=\"UTF-8\"\n\n<div>x</div>\n--000000abc--\n"
        ),
        id="base64-part-with-its-encoding-in-the-signed-body",
    ),
    pytest.param(
        lambda code: _crlf(
            head('multipart/alternative; boundary="B"')
            + f'\n\n--B\nContent-Type: text/plain; charset="utf-8"\n\nsouhlasím {code}\n--B\n'
            + "Content-Type: text/html\n\n<div>x</div>\n--B--\n"
        ),
        id="blank-lines-before-the-first-delimiter",
    ),
    pytest.param(
        lambda code: _crlf(
            head("multipart/mixed;\n boundary=B")
            + f"--B\nContent-Type: text/plain; charset=utf-8\n\nsouhlasím {code}\n--B\n"
            + 'Content-Type: application/pdf\nContent-Disposition: attachment; filename="a.pdf"\n\nx\n--B--\n'
        ),
        id="mixed-with-an-attachment-and-a-folded-header",
    ),
]


@pytest.mark.parametrize("build", GENUINE)
async def test_genuine_approval_is_accepted(harness, keys, build):
    pending = await harness.request()
    harness.deliver(dkim_sign(build(pending.code), keys["approver"]))
    result = await harness.send(pending)
    assert result["status"] == "sent", result
