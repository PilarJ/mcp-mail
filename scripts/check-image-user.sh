#!/bin/sh
# Fails unless the built image runs as UID/GID 10001 (never root) and that
# user can write to its data volume. Usage: scripts/check-image-user.sh IMAGE
set -eu

image="${1:?usage: $0 IMAGE}"
expected_id=10001

# Default entrypoint/user of the image, a fresh anonymous volume for /data.
actual=$(docker run --rm "$image" sh -c 'echo "$(id -u) $(id -g) $(id -G)"')
echo "uid gid groups: $actual"

set -- $actual
if [ "$1" != "$expected_id" ] || [ "$2" != "$expected_id" ]; then
    echo "FAIL: expected uid=$expected_id gid=$expected_id" >&2
    exit 1
fi
shift 2
for gid in "$@"; do
    if [ "$gid" = 0 ]; then
        echo "FAIL: runtime user is in the root group" >&2
        exit 1
    fi
done

docker run --rm "$image" sh -c \
    'touch "$DATA_DIR/.write-check" "$FASTMCP_HOME/.write-check"' \
    || { echo "FAIL: cannot write to \$DATA_DIR or \$FASTMCP_HOME" >&2; exit 1; }

echo "OK: runs as $expected_id:$expected_id and can write its data"
