#!/bin/sh
# PUID/PGID drop-privileges entrypoint (linuxserver.io convention).
# Unset -> run as root exactly like before (backward compatible).
# Set   -> the app runs as that uid:gid, so files Kira creates on the
#          SMB-shared media mount belong to YOUR user, not root.
set -e

if [ -n "$PUID" ] && [ -n "$PGID" ]; then
    if ! getent group kira >/dev/null 2>&1; then
        groupadd -o -g "$PGID" kira
    fi
    if ! id kira >/dev/null 2>&1; then
        useradd -o -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin kira
    fi
    # /config must be writable by the app user (the media mount's ownership
    # is the HOST's business — never chown a user's library).
    chown -R "$PUID:$PGID" /config 2>/dev/null || true
    exec gosu kira "$@"
fi
exec "$@"
