#!/bin/sh
TASK_ROOT=/Users/pilarj/.dodaios/data/society/tester/workspace
OUT="$TASK_ROOT/evidence/mcp-mail-14-4b4a04b-followup"
cd "$TASK_ROOT/repos/mcp-mail-qa14-4b4a04b-followup" || exit 2
uv run --python 3.12 --with pytest --with pytest-asyncio python -m pytest -q > "$OUT/suite.log" 2>&1
status=$?
printf '%s\n' "$status" > "$OUT/suite.exit"
exit "$status"
