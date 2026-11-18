#!/bin/sh
root=/Users/pilarj/.dodaios/data/society/tester/workspace
out="$root/evidence/mcp-mail-12-37806ea"
cd "$root/repos/mcp-mail-qa12-37806ea" || exit 2
uv run --python 3.12 --with pytest --with pytest-asyncio python "$out/check-independent.py" "$PWD" "$out" > "$out/independent.log" 2>&1
echo "$?" > "$out/independent.exit"
