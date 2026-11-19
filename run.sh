#!/bin/sh
ROOT=/Users/pilarj/.dodaios/data/society/tester/workspace
cd "$ROOT" || exit 2
export PYTHONPATH="$ROOT/repos/mcp-mail-qa14-acb569b-routine/src"
PY="$ROOT/repos/mcp-mail-qa14-7266733-routine/.venv/bin/python"
"$PY" -m pytest -c "$ROOT/repos/mcp-mail-qa14-acb569b-routine/pyproject.toml" "$ROOT/repos/mcp-mail-qa14-acb569b-routine/tests" -q > evidence/mcp-mail-14-acb569b-routine/suite.log 2>&1
printf '%s\n' "$?" > evidence/mcp-mail-14-acb569b-routine/suite.exit
"$PY" evidence/mcp-mail-14-acb569b-routine/check-independent.py > evidence/mcp-mail-14-acb569b-routine/independent.log 2>&1
rc=$?
printf '%s\n' "$rc" > evidence/mcp-mail-14-acb569b-routine/independent.exit
exit "$rc"
