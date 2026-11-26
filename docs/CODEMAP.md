# mcp-mail code map

mcp-mail exposes one IMAP/SMTP mailbox to MCP clients over Streamable HTTP.
GitHub OAuth controls who can connect, and server-side recipient rules control where mail can go.

## Directory structure

| Path | What lives there |
| --- | --- |
| [`src/mail_mcp/`](../src/mail_mcp/) | The installable Python package, including MCP tools, mailbox protocols, configuration and policy. |
| [`tests/`](../tests/) | Pytest coverage, protocol fakes, shared fixtures and wire-level test support. |
| [`.github/workflows/`](../.github/workflows/) | The GitHub Actions test workflow for pull requests and pushes to `main`. |
| [`scripts/`](../scripts/) | Repository checks run by CI, such as the container runtime-user check. |
| [`docs/`](.) | Repository navigation and developer reference. The [README](../README.md) covers setup, configuration and tool behavior. |

## Entry points

- [`pyproject.toml`](../pyproject.toml) declares Python 3.12 or newer, the runtime dependencies, the `dev` test extras and the `mail-mcp` console script. The script calls `mail_mcp.server:main`.
- [`server.py`](../src/mail_mcp/server.py) is the startup entry point for the FastMCP server and GitHub OAuth configuration. MCP clients use `<BASE_URL>/mcp`. The GitHub OAuth app uses `<BASE_URL>/auth/callback`. Set `BASE_URL` to the client-reachable URL without a trailing slash.
- [`Dockerfile`](../Dockerfile) installs the package into a Python 3.15 slim image, runs `mail-mcp` as `app` (UID/GID 10001), exposes port 8000 and uses `/data` for persistent state.

## Where to make changes

| Area | Start here |
| --- | --- |
| Configuration and login access | [`settings.py`](../src/mail_mcp/settings.py) for configuration, [`auth.py`](../src/mail_mcp/auth.py) and [`server.py`](../src/mail_mcp/server.py) for authentication and startup. |
| MCP tool inputs, results and registration | [`tools.py`](../src/mail_mcp/tools.py). Follow the relevant tool into the mail protocol or policy module when changing its behavior. |
| Mailbox reads and updates | [`imap.py`](../src/mail_mcp/imap.py) for operations such as list, search, fetch and move. [`bodystructure.py`](../src/mail_mcp/bodystructure.py) handles the server's MIME structure metadata used by summary tools. |
| Message and attachment output | [`render.py`](../src/mail_mcp/render.py) for message rendering and [`attachments.py`](../src/mail_mcp/attachments.py) for readable attachment extraction. |
| Sending and transport | [`smtp.py`](../src/mail_mcp/smtp.py) for outbound mail and [`tls.py`](../src/mail_mcp/tls.py) for mail transport TLS. |
| Send policy and approvals | [`guard.py`](../src/mail_mcp/guard.py) for outbound policy and [`approval.py`](../src/mail_mcp/approval.py) for the one-time approval path and its persisted state. |

For regressions, look at tests named for the behavior. For example, `test_mark_seen_on_get.py` covers read flags, `test_readable_attachments.py` covers readable attachment output, `test_sent_copy_and_search.py` covers Sent copies and search, and `test_approval.py` covers approvals.

## Run locally

From the repository root, create `.env` from the template in a fresh checkout. Configure it for a dedicated test mailbox and GitHub OAuth app, using the [README setup and configuration](../README.md#setup) as the reference. Preserve an existing test configuration if you already have one.

```bash
cp -n .env.example .env
DOCKER_CONTEXT=colima-qa docker build -t mcp-mail-codemap-qa .
DOCKER_CONTEXT=colima-qa docker run --rm --name mcp-mail-codemap-qa \
  --env-file .env -p 127.0.0.1:8000:8000 mcp-mail-codemap-qa
```

The container runs in the foreground. Stop it with Ctrl+C to remove the container and its anonymous `/data` volume. For connector testing, the client and its OAuth callback flow must be able to reach the configured `BASE_URL`. This loopback binding serves clients on the local machine.

## Run tests

Use Python 3.15 to match CI. From the repository root in a shell with Python 3.15 available:

```bash
python3.15 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -v --tb=short
```

The last two commands are the ones in [the test workflow](../.github/workflows/test.yml). CI runs on Ubuntu with a ten-minute job timeout. A second CI job builds the image and runs [`check-image-user.sh`](../scripts/check-image-user.sh), which fails unless the container runs as UID/GID 10001 and can write `/data` and `/data/fastmcp`. Run it locally with:

```bash
DOCKER_CONTEXT=colima-qa docker build -t mcp-mail-codemap-qa .
DOCKER_CONTEXT=colima-qa scripts/check-image-user.sh mcp-mail-codemap-qa
```

The pytest suite uses fakes for IMAP, SMTP and DNS, with real TLS over loopback for certificate tests. It needs no Docker service, live mailbox or OAuth credentials.

## Gotchas

- One instance serves one mailbox. Select the mailbox by selecting its connector. GitHub authenticates the operator, while the configured IMAP and SMTP credentials authenticate the server to the mailbox.
- The login allowlist is required at startup. An empty recipient allowlist disables ordinary sending, including replies. `confirm=true` guards against mistakes, and the recipient allowlist enforces the outbound boundary. Treat bodies and extracted attachments as untrusted content.
- The optional approval tools require `APPROVER_ADDRESS`. An approval binds one recipient and exact message content, is verified against a DKIM-signed reply in `INBOX`, and is consumed before SMTP. An SMTP failure requires a new approval.
- IMAP and SMTP need hostnames that match their certificates. Use the provider's actual mail hostname if the domain is behind a CDN. Set `SSL_CERT_FILE` for a private CA. Operations open short-lived connections in worker threads and log in on each call.
- `mail_get` marks a message read by default. Use `MARK_SEEN_ON_GET=false` or the per-call `mark_seen` argument to control that side effect. `mail_delete` requires explicit enablement and permanently expunges.
- `SENT_FOLDER` must name an existing IMAP folder. SMTP success still counts as a send if filing the copy fails, so check `sent_copy` before diagnosing a missing Sent item.
- The container's `/data` holds `sent_log.json`, `approvals.sqlite3` and FastMCP's OAuth state under `/data/fastmcp`. Persistent deployments need to retain that state across container replacement. Set a stable `JWT_SIGNING_KEY` to keep client tokens valid across restarts.
