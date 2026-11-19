#!/bin/sh
ROOT=/Users/pilarj/.dodaios/data/society/tester/workspace
cd "$ROOT" || exit 2
export PYTHONPATH="$ROOT/repos/mcp-mail-qa14-acb569b-routine/src"
"$ROOT/repos/mcp-mail-qa14-7266733-routine/.venv/bin/python" evidence/mcp-mail-14-acb569b-routine/check-scan-rotation.py > evidence/mcp-mail-14-acb569b-routine/scan-rotation.log 2>&1
rc=$?
printf '%s\n' "$rc" > evidence/mcp-mail-14-acb569b-routine/scan-rotation.exit
exit "$rc"
