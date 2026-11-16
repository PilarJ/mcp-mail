"""SEND_MAX_PER_HOUR and SEND_MAX_PER_DAY: a volume ceiling whatever ALLOWED_RECIPIENTS says (#6)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from .support import (
    ALLOWLISTED,
    APPROVER,
    BODY,
    MAILBOX,
    SUBJECT,
    make_settings,
    plain_message,
)

pytestmark = pytest.mark.anyio

OPEN = {"ALLOWED_RECIPIENTS": "*"}
OTHER = "someone@example.net"
THIRD = "third@example.net"


async def send(harness, to=(ALLOWLISTED,), cc=None, subject=SUBJECT) -> dict:
    return await harness.call("mail_send", to=list(to), subject=subject, body=BODY, cc=cc, confirm=True)


def ago(**delta) -> str:
    return (datetime.now() - timedelta(**delta)).isoformat(timespec="seconds")


def seed(harness, *entries: dict) -> None:
    """Entries already in the audit log, as an earlier run of the server wrote them."""
    path = harness.settings.data_dir / "sent_log.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sent": list(entries)}))


def legacy(sent_at: str, kind: str = "send", to: str = OTHER) -> dict:
    """An entry written before this change: no send id, no recipient count."""
    return {"to": to, "subject": "earlier", "sent_at": sent_at, "kind": kind}


def assert_limit_refusal(result: dict, window: str) -> None:
    assert result["status"] == "refused", result
    assert result["error"] == "send_limit_reached"
    assert result["limit"] == window


# ---------------------------------------------------------------- unset: today's behaviour


async def test_unset_limits_keep_todays_behaviour(make_harness):
    harness = make_harness(**OPEN)
    for n in range(12):
        assert (await send(harness, subject=f"m{n}"))["status"] == "sent"
    assert len(harness.delivered()) == 12
    assert set(harness.log_kinds()) == {"send"}


async def test_unset_limits_ignore_an_unreadable_log_as_before(make_harness):
    harness = make_harness(**OPEN)
    (harness.settings.data_dir / "sent_log.json").write_text("{not json")
    assert (await send(harness))["status"] == "sent"


# ---------------------------------------------------------------- per hour


async def test_hour_limit_sends_up_to_the_limit_and_refuses_the_next(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="3", **OPEN)
    for n in range(3):
        assert (await send(harness, subject=f"m{n}"))["status"] == "sent"

    refused = await send(harness, subject="one too many")

    assert_limit_refusal(refused, "hour")
    assert "3" in refused["detail"] and "SEND_MAX_PER_HOUR" in refused["detail"]
    assert len(harness.delivered()) == 3


async def test_refusal_is_written_to_the_audit_log(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="1", **OPEN)
    await send(harness)
    await send(harness, to=[OTHER], subject="blocked")

    last = harness.log()[-1]
    assert last["kind"] == "send_limit_refused"
    assert last["to"] == OTHER
    assert last["subject"] == "blocked"
    assert last["limit"] == "hour"
    tail = (await harness.call("mail_sent_log"))["sent"]
    assert tail[-1]["kind"] == "send_limit_refused"


async def test_refusals_do_not_count_against_the_limit(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="2", **OPEN)
    seed(harness, *[
        {"to": OTHER, "subject": "x", "sent_at": ago(minutes=5), "kind": "send_limit_refused", "limit": "hour"}
        for _ in range(10)
    ])
    assert (await send(harness))["status"] == "sent"


async def test_hour_window_rolls(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="2", **OPEN)
    seed(harness, legacy(ago(minutes=61)), legacy(ago(minutes=59)))

    assert (await send(harness))["status"] == "sent"  # one in the last hour, one now
    assert_limit_refusal(await send(harness), "hour")


async def test_entries_from_before_this_change_count_one_each(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="2", **OPEN)
    seed(harness, legacy(ago(minutes=10)), legacy(ago(minutes=5), kind="reply"))
    assert_limit_refusal(await send(harness), "hour")


# ---------------------------------------------------------------- per day


async def test_day_limit_counts_past_the_hour(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="10", SEND_MAX_PER_DAY="3", **OPEN)
    seed(harness, legacy(ago(hours=23)), legacy(ago(hours=5)), legacy(ago(hours=2)))

    assert_limit_refusal(await send(harness), "day")
    assert harness.delivered() == []


async def test_day_window_rolls(make_harness):
    harness = make_harness(SEND_MAX_PER_DAY="2", **OPEN)
    seed(harness, legacy(ago(hours=24, minutes=1)), legacy(ago(hours=3)))

    assert (await send(harness))["status"] == "sent"
    assert_limit_refusal(await send(harness), "day")


async def test_hour_limit_is_reported_first_when_both_are_reached(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="1", SEND_MAX_PER_DAY="1", **OPEN)
    await send(harness)
    assert_limit_refusal(await send(harness), "hour")


# ---------------------------------------------------------------- what counts


async def test_every_recipient_counts_to_and_cc(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="3", **OPEN)
    assert (await send(harness, to=[ALLOWLISTED, OTHER]))["status"] == "sent"  # 2 of 3

    refused = await send(harness, to=[THIRD], cc=[OTHER])  # would be 4

    assert_limit_refusal(refused, "hour")
    assert (await send(harness, to=[THIRD]))["status"] == "sent"  # exactly 3
    assert_limit_refusal(await send(harness, to=[THIRD]), "hour")


async def test_one_message_to_more_recipients_than_the_limit_is_refused(make_harness):
    harness = make_harness(SEND_MAX_PER_DAY="2", **OPEN)
    assert_limit_refusal(await send(harness, to=[ALLOWLISTED, OTHER, THIRD]), "day")
    assert harness.delivered() == []


async def test_reply_is_limited_too(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="1", **OPEN)
    uid = str(harness.deliver(plain_message("Dobrý den", from_=f"Someone <{OTHER}>")).uid)
    await send(harness)

    refused = await harness.call("mail_reply", uid=uid, body="Díky", confirm=True)

    assert_limit_refusal(refused, "hour")
    assert len(harness.delivered()) == 1
    assert harness.log()[-1]["kind"] == "send_limit_refused"


async def test_a_reply_counts_against_the_limit(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="1", **OPEN)
    uid = str(harness.deliver(plain_message("Dobrý den", from_=f"Someone <{OTHER}>")).uid)
    assert (await harness.call("mail_reply", uid=uid, body="Díky", confirm=True))["status"] == "sent"
    assert_limit_refusal(await send(harness), "hour")


async def test_approval_requests_do_not_count(make_harness):
    harness = make_harness(APPROVER_ADDRESS=APPROVER, SEND_MAX_PER_HOUR="1")
    await harness.request()
    await harness.request(subject="Druhá")
    assert (await send(harness))["status"] == "sent"


async def test_an_approved_send_counts(make_harness):
    harness = make_harness(APPROVER_ADDRESS=APPROVER, SEND_MAX_PER_HOUR="1")
    pending = await harness.request()
    harness.approve(pending)
    assert (await harness.send(pending))["status"] == "sent"
    assert_limit_refusal(await send(harness), "hour")


async def test_refusals_before_the_limit_check_are_not_counted(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="1", DEDUP_MINUTES="60", **OPEN)
    unconfirmed = await harness.call("mail_send", to=[OTHER], subject=SUBJECT, body=BODY, confirm=False)
    assert unconfirmed["status"] == "refused" and "error" not in unconfirmed
    assert (await send(harness, to=[OTHER]))["status"] == "sent"
    # Dedup refuses before the limit is looked at, and a dedup refusal logs nothing.
    again = await send(harness, to=[OTHER])
    assert again["status"] == "refused" and "error" not in again
    assert harness.log_kinds() == ["send"]


async def test_smtp_failure_is_not_counted(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="1", **OPEN)
    harness.smtp_failure["error"] = OSError("connection reset")
    with pytest.raises(ToolError):
        await send(harness)
    harness.smtp_failure["error"] = None
    assert (await send(harness))["status"] == "sent"


# ---------------------------------------------------------------- restart, state, concurrency


async def test_restart_does_not_reset_the_count(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="2", **OPEN)
    await send(harness)
    await send(harness)

    harness.build()  # what a server restart does

    assert_limit_refusal(await send(harness), "hour")


async def test_unreadable_log_refuses_when_a_limit_is_set(make_harness):
    harness = make_harness(SEND_MAX_PER_DAY="50", **OPEN)
    path = harness.settings.data_dir / "sent_log.json"
    for broken in ("{not json", '{"sent": "x"}', '["x"]', '{"sent": [1]}'):
        path.write_text(broken)

        result = await send(harness)

        assert result["status"] == "refused", broken
        assert result["error"] == "sent_log_unreadable"
        assert harness.delivered() == []
        assert path.read_text() == broken  # the evidence is left as it was


async def test_a_missing_log_is_an_empty_log(make_harness):
    harness = make_harness(SEND_MAX_PER_DAY="1", **OPEN)
    assert not (harness.settings.data_dir / "sent_log.json").exists()
    assert (await send(harness))["status"] == "sent"


async def test_concurrent_sends_cannot_pass_the_limit_together(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="1", **OPEN)
    results = await asyncio.gather(*(send(harness, subject=f"m{n}") for n in range(5)))

    assert sorted(r["status"] for r in results) == ["refused"] * 4 + ["sent"]
    assert len(harness.delivered()) == 1


async def test_log_keeps_every_counted_entry_of_the_day_past_500(make_harness):
    harness = make_harness(SEND_MAX_PER_DAY="1000", **OPEN)
    seed(harness, *[legacy(ago(days=3)) for _ in range(100)], *[legacy(ago(hours=2)) for _ in range(600)])

    assert (await send(harness))["status"] == "sent"

    kept = harness.log()
    # Older entries go as before; the ones the day limit still needs stay.
    assert len(kept) == 601
    assert all(entry["sent_at"] >= ago(days=1) for entry in kept)


async def test_without_limits_the_log_is_still_cut_at_500(make_harness):
    harness = make_harness(**OPEN)
    seed(harness, *[legacy(ago(hours=2)) for _ in range(600)])
    await send(harness)
    assert len(harness.log()) == 500


# ---------------------------------------------------------------- configuration and status


@pytest.mark.parametrize("name", ["SEND_MAX_PER_HOUR", "SEND_MAX_PER_DAY"])
@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_a_limit_below_one_or_not_a_number_stops_the_server(tmp_path, name, value):
    with pytest.raises(ValidationError):
        make_settings(tmp_path, **{name: value})


@pytest.mark.parametrize("name", ["SEND_MAX_PER_HOUR", "SEND_MAX_PER_DAY"])
def test_an_empty_limit_is_unset(tmp_path, name):
    settings = make_settings(tmp_path, **{name: ""})
    assert getattr(settings, name.lower()) is None


async def test_status_reports_the_limits(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="5", SEND_MAX_PER_DAY="20")
    status = await harness.call("mail_status")
    assert status["send_limits"] == {"per_hour": 5, "per_day": 20}
    assert MAILBOX == status["mailbox"]


async def test_status_without_limits_is_unchanged(make_harness):
    harness = make_harness()
    assert "send_limits" not in await harness.call("mail_status")


async def test_a_limit_refusal_does_not_trigger_the_repeat_guard(make_harness):
    harness = make_harness(SEND_MAX_PER_HOUR="1", DEDUP_MINUTES="60", **OPEN)
    await send(harness)
    assert_limit_refusal(await send(harness, to=[OTHER]), "hour")
    entries = json.loads((harness.settings.data_dir / "sent_log.json").read_text())
    for entry in entries["sent"]:
        if entry["kind"] == "send":
            entry["sent_at"] = "2000-01-01T00:00:00"  # the sent mail leaves both windows
    (harness.settings.data_dir / "sent_log.json").write_text(json.dumps(entries))
    assert (await send(harness, to=[OTHER]))["status"] == "sent"
