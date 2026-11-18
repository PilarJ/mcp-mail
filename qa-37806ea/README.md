# PR 12 independent QA

Reviewed and executed head: 37806ea674a1f9361e4e3b9285a3e58957099557.
Scope: issue 11 and Claire's assignment. No application source changes. No merge or deployment.

## Full suite

From the pinned clone:

```sh
uv run --python 3.12 --with pytest --with pytest-asyncio python -m pytest -q
```

Result: 690 passed, one upstream Authlib deprecation warning, 25.83 seconds; Python 3.12.16. The repository's existing tests use only fake/wire IMAP and SMTP implementations. No live mailbox was read or changed.

## Independent checks

`run-independent.sh` invokes the saved `check-independent.py` using the same package environment. The real MCP tool registration, in-memory MCP Client, ImapClient, imaplib and imap-tools operate against the PR's SeenTrackingServer. Assertions examine stored message flags, including pre-existing system flags, a keyword and another message's state. The supplemental worker audit hook refuses socket connect, DNS, bind and sendto operations.

- 16 mail_get cases: instance false/true x omitted/null/false/true x initially unread/read. All explicit flag combinations pass, omitted/null use the instance, false preserves an already-read state, other flags remain and a second UID is untouched. The JSON schema advertises an optional nullable boolean with default null. Both instance-specific tool descriptions show the effective default and name the override setting.
- 24 non-marking cases: instance false/true x initially unread/read x list, unread-only list, search, thread, attachment and approval-candidate fetch. Actual flags remain identical; FETCH uses PEEK and no mutating command occurs. The test double does not implement full IMAP filtering: these assertions establish read-state preservation, not a new test of filtering semantics.
- Four real startup subprocesses with synthetic configuration: maybe, 2, truee and none all exit nonzero with a boolean-validation error naming MARK_SEEN_ON_GET. The children receive only a minimal synthetic environment and a sitecustomize socket audit guard. Validation happens before any network attempt or server bind. Raw stderr is included.

The standard suite also covers default true, empty -> true, valid boolean strings, and the existing read-tool guards. README's reading section/settings table and .env.example agree with the implementation. Only settings, mail_get registration and documentation change in production; sending, approval logic, auth, mail_mark and IMAP methods are unchanged.

No HTML presentation changed; screenshots and keyboard checks do not apply. No repository QA skill exists in this clone. Scope was established from issue 11, README, .env.example, the patch and the assignment; the shared wiki reader is not available in this worker.

## CI and handoff

The pytest job is SUCCESS in https://github.com/PilarJ/mcp-mail/actions/runs/44256384798 on the exact head. No P1-P3 finding or outstanding QA item. After the published review and qa-ok label, Claire / Projekťák own the handoff for the project's feature-merge process. This QA does not merge or deploy.

The generated uv.lock is an untracked local dependency-resolution artifact; application source is unchanged. Raw execution logs, exact runtime package versions, scenarios and stderr are preserved. SHA256SUMS covers the stable evidence files.
