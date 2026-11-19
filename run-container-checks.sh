#!/bin/sh
TASK_ROOT=/Users/pilarj/.dodaios/data/society/tester/workspace
OUT="$TASK_ROOT/evidence/mcp-mail-14-4b4a04b-followup"
cd "$TASK_ROOT" || exit 2
for revision in acb569b 4b4a04b; do
  NAME="tester-mail14-app-$revision-followup"
  docker cp "$OUT/format-fixtures" "$NAME:/tmp/qa-fixtures"
  docker cp "$OUT/czech-valid-font.pdf" "$NAME:/tmp/qa-fixtures/české-stránky-opravené.pdf"
  docker cp "$OUT/check-container.py" "$NAME:/tmp/qa-check.py"
  docker exec "$NAME" python /tmp/qa-check.py > "$OUT/container-$revision.json" 2> "$OUT/container-$revision.stderr"
  printf '%s\n' "$?" > "$OUT/container-$revision.exit"
done
