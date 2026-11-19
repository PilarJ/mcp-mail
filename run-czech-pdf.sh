#!/bin/sh
TASK_ROOT=/Users/pilarj/.dodaios/data/society/tester/workspace
OUT="$TASK_ROOT/evidence/mcp-mail-14-4b4a04b-followup"
PY="$TASK_ROOT/repos/mcp-mail-qa14-7266733-routine/.venv/bin/python"
cd "$TASK_ROOT" || exit 2
for revision in acb569b 4b4a04b; do
  if [ "$revision" = acb569b ]; then SOURCE=repos/mcp-mail-qa14-acb569b-routine; else SOURCE=repos/mcp-mail-qa14-4b4a04b-followup; fi
  PYTHONPATH="$TASK_ROOT/$SOURCE/src" "$PY" "$OUT/check-czech-pdf.py" "$revision" > "$OUT/czech-pdf-$revision.log" 2>&1
  printf '%s\n' "$?" > "$OUT/czech-pdf-$revision.exit"
done
