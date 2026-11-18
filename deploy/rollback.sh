#!/bin/sh
# Roll back to the previous shim.  The shim and its unit were never removed.
#
#     sudo ./deploy/rollback.sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "must run as root" >&2
    exit 1
fi

systemctl disable --now goodix-touch.service 2>/dev/null || true
systemctl enable touch-filter.service
systemctl restart touch-filter.service

sleep 1
echo "touch-filter.service: $(systemctl is-active touch-filter.service)"
journalctl -u touch-filter.service -n 5 --no-pager 2>/dev/null | sed 's/^/  /' || true
