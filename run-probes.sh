#!/bin/sh
TASK_ROOT=/Users/pilarj/.dodaios/data/society/tester/workspace
OUT="$TASK_ROOT/evidence/mcp-mail-14-4b4a04b-followup"
PY="$TASK_ROOT/repos/mcp-mail-qa14-7266733-routine/.venv/bin/python"
cd "$TASK_ROOT" || exit 2
for revision in acb569b 4b4a04b; do
  if [ "$revision" = acb569b ]; then SOURCE=repos/mcp-mail-qa14-acb569b-routine; else SOURCE=repos/mcp-mail-qa14-4b4a04b-followup; fi
  PYTHONPATH="$TASK_ROOT/$SOURCE/src" "$PY" "$OUT/check-formats.py" "$revision" > "$OUT/formats-$revision.log" 2>&1
  printf '%s\n' "$?" > "$OUT/formats-$revision.exit"
done
export PYTHONPATH="$TASK_ROOT/repos/mcp-mail-qa14-4b4a04b-followup/src"
"$PY" "$OUT/check-independent.py" > "$OUT/independent.log" 2>&1
printf '%s\n' "$?" > "$OUT/independent.exit"
"$PY" "$OUT/check-scan-rotation.py" > "$OUT/scan-rotation.log" 2>&1
printf '%s\n' "$?" > "$OUT/scan-rotation.exit"
printf '0\n' > "$OUT/probes-complete.exit"
