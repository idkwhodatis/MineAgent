#!/bin/sh
# Runtime-only prefix rendering; image/assets stay identical for every prefix.
set -eu
LC_ALL=C
export LC_ALL
prefix=${SUBPATH:-}
[ "$prefix" != / ] || prefix=
case "$prefix" in *[!A-Za-z0-9/._-]*) echo 'Invalid SUBPATH' >&2; exit 64;; esac
if [ "${#prefix}" -gt 200 ] || ! printf '%s\n' "$prefix" | grep -Eq '^(/[A-Za-z0-9._-]+)*$'; then
    echo 'Invalid SUBPATH' >&2; exit 64
fi
case "$prefix/" in */./*|*/../*) echo 'Invalid SUBPATH' >&2; exit 64;; esac
source=/usr/share/nginx/html/index.html
destination=/tmp/mineagent-index.html
if [ "$#" -ne 0 ]; then
    [ "$#" -eq 3 ] && [ "$1" = --render-only ] || exit 64
    source=$2; destination=$3
fi
grep -q 'data-mineagent-base href="/"' "$source"
grep -q 'name="mineagent-subpath" content=""' "$source"
umask 077
sed -e "s|data-mineagent-base href=\"/\"|data-mineagent-base href=\"$prefix/\"|" \
    -e "s|name=\"mineagent-subpath\" content=\"\"|name=\"mineagent-subpath\" content=\"$prefix\"|" \
    "$source" > "$destination"
chmod 0644 "$destination"
[ "$#" -eq 0 ] || exit 0
exec nginx -g 'daemon off;'
