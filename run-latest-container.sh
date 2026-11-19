#!/bin/sh
set -eu
TASK_ROOT=/Users/pilarj/.dodaios/data/society/tester/workspace
OUT="$TASK_ROOT/evidence/mcp-mail-14-4b4a04b-followup/12c554f"
NAME=tester-mail14-app-12c554f-followup
docker cp "$OUT/format-fixtures" "$NAME:/tmp/qa-fixtures"
docker cp "$OUT/czech-valid-font.pdf" "$NAME:/tmp/qa-fixtures/české-stránky-opravené.pdf"
docker cp "$OUT/check-container.py" "$NAME:/tmp/qa-check.py"
docker exec "$NAME" python /tmp/qa-check.py > "$OUT/container-results.json" 2> "$OUT/container.stderr"
printf '0\n' > "$OUT/container.exit"
docker logs "$NAME" > "$OUT/app.log" 2>&1
