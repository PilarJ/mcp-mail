#!/bin/sh
set -eu
TASK_ROOT=/Users/pilarj/.dodaios/data/society/tester/workspace
OUT="$TASK_ROOT/evidence/mcp-mail-14-4b4a04b-followup"
cd "$TASK_ROOT"
cp evidence/mcp-mail-14-acb569b-routine/Dockerfile.dovecot "$OUT/Dockerfile.dovecot"
cp evidence/mcp-mail-14-acb569b-routine/dovecot.conf "$OUT/dovecot.conf"
docker build -f "$OUT/Dockerfile.dovecot" -t tester-mail14-dovecot-followup "$OUT" > "$OUT/dovecot-build.log" 2>&1
docker run -d --name tester-mail14-dovecot-followup -p 127.0.0.1:19924:993 tester-mail14-dovecot-followup > "$OUT/dovecot-container.id"
docker cp tester-mail14-dovecot-followup:/etc/dovecot/qa.crt "$OUT/qa.crt" > "$OUT/cert-copy.log" 2>&1
for revision in acb569b 4b4a04b; do
  if [ "$revision" = acb569b ]; then SOURCE=repos/mcp-mail-qa14-acb569b-routine; PORT=19925; else SOURCE=repos/mcp-mail-qa14-4b4a04b-followup; PORT=19926; fi
  docker build -t "tester-mail14-app-$revision-followup" "$SOURCE" > "$OUT/build-$revision.log" 2>&1
  printf '0\n' > "$OUT/build-$revision.exit"
  docker run -d --name "tester-mail14-app-$revision-followup" -p "127.0.0.1:$PORT:8000" \
    -e MAILBOX_LABEL=qa@example.test -e BASE_URL="http://localhost:$PORT" \
    -e GITHUB_CLIENT_ID=qa-dummy -e GITHUB_CLIENT_SECRET=qa-dummy \
    -e ALLOWED_GITHUB_LOGINS=qa-fixture -e IMAP_HOST=localhost -e IMAP_USER=qa@example.test \
    -e IMAP_PASS=qa-dummy -e SMTP_HOST=localhost -e SMTP_USER=qa@example.test -e SMTP_PASS=qa-dummy \
    -e ALLOWED_RECIPIENTS= "tester-mail14-app-$revision-followup" > "$OUT/app-$revision-container.id"
done
printf '0\n' > "$OUT/build-complete.exit"
