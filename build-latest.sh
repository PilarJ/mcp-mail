#!/bin/sh
set -eu
TASK_ROOT=/Users/pilarj/.dodaios/data/society/tester/workspace
OUT="$TASK_ROOT/evidence/mcp-mail-14-4b4a04b-followup/12c554f"
cd "$TASK_ROOT"
docker build -t tester-mail14-app-12c554f-followup repos/mcp-mail-qa14-12c554f-followup > "$OUT/build.log" 2>&1
printf '0\n' > "$OUT/build.exit"
docker run -d --name tester-mail14-app-12c554f-followup -p 127.0.0.1:19927:8000 \
  -e MAILBOX_LABEL=qa@example.test -e BASE_URL=http://localhost:19927 \
  -e GITHUB_CLIENT_ID=qa-dummy -e GITHUB_CLIENT_SECRET=qa-dummy -e ALLOWED_GITHUB_LOGINS=qa-fixture \
  -e IMAP_HOST=localhost -e IMAP_USER=qa@example.test -e IMAP_PASS=qa-dummy \
  -e SMTP_HOST=localhost -e SMTP_USER=qa@example.test -e SMTP_PASS=qa-dummy -e ALLOWED_RECIPIENTS= \
  tester-mail14-app-12c554f-followup > "$OUT/container.id"
printf '0\n' > "$OUT/build-complete.exit"
