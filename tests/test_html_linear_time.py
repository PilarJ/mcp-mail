"""html_to_text runs in linear time on hostile HTML (security review 5976680729, High).

HTML mail bodies and .html/.htm/.eml attachments (up to 5 MB) are written by whoever
sent the mail. Unclosed <script>/<style> elements and a '<' with no '>' after it made
the old regexes scan to the end of the text from every opening, so the time grew with
the square of the length: 160 kB of '<script>' took about 5 s, 5 MB well over an hour.

The output must stay what the old regexes produced; only the time changes.
"""

from __future__ import annotations

import random
import re
import time
from email.message import EmailMessage

import pytest

from mail_mcp import attachments
from mail_mcp.render import html_to_text

# Each input takes several seconds with the old regexes and milliseconds in linear time.
HOSTILE = {
    "unclosed script": "<script>" * 20_000,
    "unclosed style": "<style>" * 20_000,
    "script opening without >": "<script" * 20_000,
    "mixed unclosed script and style": "<script><style>" * 10_000,
    "tag without >": "<a " * 70_000,
    "lone <": "<" * 200_000,
}
LIMIT_S = 1.0


def timed(fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    return result, time.perf_counter() - start


@pytest.mark.parametrize("html", HOSTILE.values(), ids=HOSTILE.keys())
def test_html_to_text_is_fast_on_hostile_html(html):
    _, seconds = timed(html_to_text, html)
    assert seconds < LIMIT_S, f"{len(html)} chars took {seconds:.2f} s"


@pytest.mark.parametrize("unit", ["<script>", "<a "], ids=["script", "tag"])
def test_time_grows_linearly_not_with_the_square(unit):
    small = timed(html_to_text, unit * 10_000)[1]
    large = timed(html_to_text, unit * 40_000)[1]
    # 4x the input: linear is about 4x, quadratic about 16x. Floor for timer noise.
    assert large < max(small, 0.005) * 8, f"{small:.4f} s -> {large:.4f} s"


def test_html_attachment_with_unclosed_scripts_is_fast():
    payload = b"<html><body><p>Faktura</p>" + b"<script>" * 20_000 + b"</body></html>"
    readable, seconds = timed(attachments.read, "x.html", "text/html", payload)
    assert seconds < LIMIT_S, f"{seconds:.2f} s"
    assert "Faktura" in readable.text


def test_eml_attachment_with_unclosed_scripts_is_fast():
    inner = EmailMessage()
    inner["Subject"] = "Forwarded"
    inner.set_content("<p>Faktura</p>" + "<script>" * 20_000, subtype="html")
    readable, seconds = timed(attachments.read, "x.eml", "message/rfc822", inner.as_bytes())
    assert seconds < LIMIT_S, f"{seconds:.2f} s"
    assert "Faktura" in readable.text


# --- same output as the old regexes --------------------------------------------------

_OLD_TAG_RE = re.compile(r"<[^>]+>")
_OLD_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def old_html_to_text(html: str) -> str:
    text = _OLD_SCRIPT_RE.sub("", html or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|h[1-6]|li|tr|table)\s*>", "\n", text, flags=re.IGNORECASE)
    text = _OLD_TAG_RE.sub("", text)
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
        ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
    ):
        text = text.replace(entity, char)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


SAMPLES = [
    "",
    "plain text",
    "<p>Hello</p><script>alert(1)</script><p>World</p>",
    '<STYLE type="text/css">p{}</STYLE>Body<br/>line',
    "<Script>x</SCRIPT>kept",
    "<script>never closed<p>text after</p>",
    "<style>a</style><script>b</style>c</script>d",
    "<style>a<script>b</style>c</script>d",
    "<script>a<style>b</script>c</style>d",
    "<scripted>not a script</scripted>tail",
    "<script\n>multi\nline</script>after",
    "a < b and c > d",
    "<<a>> <> <",
    "1 < 2",
    "<p>unterminated <b",
    "&lt;script&gt;shown&lt;/script&gt;",
]


@pytest.mark.parametrize("html", SAMPLES)
def test_same_output_as_the_old_regexes(html):
    assert html_to_text(html) == old_html_to_text(html)


def test_same_output_as_the_old_regexes_on_random_html():
    pieces = ["<script>", "</script>", "<style a=1>", "</style>", "<SCRIPT>", "</Script>",
              "<script", "<", ">", "<p>", "</p>", "<br>", "x", " ", "\n", "&amp;", "</div >"]
    rng = random.Random(13)
    for _ in range(3_000):
        html = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 25)))
        assert html_to_text(html) == old_html_to_text(html), repr(html)
