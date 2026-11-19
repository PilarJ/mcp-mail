#!/bin/sh
TASK_ROOT=/Users/pilarj/.dodaios/data/society/tester/workspace
OUT="$TASK_ROOT/evidence/mcp-mail-14-4b4a04b-followup/12c554f"
PY="$TASK_ROOT/repos/mcp-mail-qa14-7266733-routine/.venv/bin/python"
cd "$TASK_ROOT/repos/mcp-mail-qa14-12c554f-followup" || exit 2
uv run --python 3.12 --with pytest --with pytest-asyncio python -m pytest -q > "$OUT/suite.log" 2>&1
printf '%s\n' "$?" > "$OUT/suite.exit"
cd "$TASK_ROOT" || exit 2
export PYTHONPATH="$TASK_ROOT/repos/mcp-mail-qa14-12c554f-followup/src"
"$PY" "$OUT/check-formats.py" 12c554f > "$OUT/formats-12c554f.log" 2>&1
printf '%s\n' "$?" > "$OUT/formats-12c554f.exit"
"$PY" "$OUT/check-czech-pdf.py" 12c554f > "$OUT/czech-pdf-12c554f.log" 2>&1
printf '%s\n' "$?" > "$OUT/czech-pdf-12c554f.exit"
"$PY" "$OUT/check-independent.py" > "$OUT/independent.log" 2>&1
printf '%s\n' "$?" > "$OUT/independent.exit"
"$PY" "$OUT/check-scan-rotation.py" > "$OUT/scan-rotation.log" 2>&1
printf '%s\n' "$?" > "$OUT/scan-rotation.exit"
printf '0\n' > "$OUT/tests-complete.exit"
