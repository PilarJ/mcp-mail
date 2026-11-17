# QA delta re-verification: mcp-mail PR 7

Verdict: PASS on 1f486ba78e592aec7a280d80f2264e2c89a334e3.
Defect baseline: 2492ac190214bcf1b12a0180a1afd852229f6161.
Scope: developer request https://github.com/PilarJ/mcp-mail/pull/7#issuecomment-6942246394.

## Independently executed results

- Full repository suite at head: 660 passed, zero failures or skips, one existing Authlib deprecation warning; 22.63 seconds. The actual chmod-000 test passed as uid 501, not root.
- The exact current test_send_limits.py, conftest.py and support.py copied outside the product checkout, against baseline src: 33 failed, 42 passed. All 35 original limit cases still pass. The 40 new cases contribute 33 expected failures and seven unchanged-behavior passes. Failures show incorrect approval_requested responses, malformed-shape exceptions, incorrect approval consumption and sent-after-log-error behavior, rather than collection/setup errors.
- All 75 limit cases pass within the head full suite.
- Six corrupt contents tested: two invalid JSON strings; sent as a string; a top-level list; non-object entries; and sent_at as a number.
- With APPROVER_ADDRESS and SEND_MAX_PER_HOUR=2: two successful fake deliveries, damage the log, then ordinary send / request approval / ordinary send all refuse with sent_log_unreadable. No approval mail is emitted and file contents are preserved. The chmod-000 variant additionally checks unchanged permissions before restoring them for cleanup and comparing bytes.
- Approved and modified-approved messages refuse without spending or voiding their approval when the log is broken. After repairing the fixture, the same approval still sends.
- DEDUP_MINUTES=60 with malformed data: clean refusal and unchanged bytes when limited; sending and approved sending succeed when unlimited. Unlimited approval-request compatibility also passes.
- If corruption occurs after SMTP, the already delivered mail is reported sent while the broken file stays intact and later limited sends fail closed.

These are local executions of repository regression tests, using repository fake SMTP/IMAP/DNS support, not independent transport integration or live mail. No real message, live service, mailbox or production file was accessed. There is no UI change; browser/visual checks and Compose naming checks are not applicable. The project wiki card was not available through exposed tools; repository documentation and the scoped PR request were read.

## Commands

Workspace: /Users/pilarj/.dodaios/data/society/tester/workspace
Python: qa-evidence/mcp-mail-pr7-2492ac1/venv/bin/python (existing declared-dev-dependency Python 3.12 environment).
Both executions cleared inherited environment with env -i and set PATH=/usr/bin:/bin.

Head, cwd repos/mcp-mail-qa7-1f486ba:

    PYTHONPATH=<workspace>/repos/mcp-mail-qa7-1f486ba/src <python> -m pytest -v --tb=short

Baseline, cwd workspace:

    PYTHONPATH=<workspace>/repos/mcp-mail-qa7-2492ac1/src <python> -m pytest -c repos/mcp-mail-qa7-1f486ba/pyproject.toml -v --tb=short qa-evidence/mcp-mail-pr7-1f486ba/current-tests/tests/test_send_limits.py

The copied test's SHA256 matches the head original; see provenance.json. PYTHONPATH pins the selected implementation despite reusing the earlier venv.

## CI provenance

Retrieved CI: https://github.com/PilarJ/mcp-mail/actions/runs/44042769236/job/133901092246
CI checked out synthetic merge 71ccefb0469a21a325fb891e93e5183bc102ba18, with parents 657bbcbe55280a17b78c91d27f73b17395788327 and the reviewed 1f486ba head. Its tree and the reviewed head tree are both 3d52e39551b00b8647e8c1fbfebc1a14629bee13. This establishes source-tree equivalence, not commit or environment identity. The workflow uses Python 3.12, installs declared dev dependencies, and runs pytest. No behavior in this delta depends on commit identity or generated artifacts. CI is distinct from the personally executed suite above.

## Cleanup and handoff

No server, container or background process was started. Pytest owns its temporary fixtures and restores chmod permissions. Product source checkouts remain unchanged. No open QA finding in this delta. Project owner controls the approval and merge decision; nothing was merged by Tester.
