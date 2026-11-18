#!/bin/sh
# Install the rewritten Goodix touchscreen driver on the kiosk.
#
# Run as root from a copy of this repository:
#     sudo ./deploy/install.sh
#
# The previous shim (/usr/local/bin/touch-filter-hid.py and
# touch-filter.service) is left untouched; this only disables it.  Roll back
# with deploy/rollback.sh.
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "must run as root" >&2
    exit 1
fi

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(dirname -- "$here")
driver="$root/src/goodix-touch-driver.py"
unit="$here/goodix-touch.service"

for f in "$driver" "$unit"; do
    [ -f "$f" ] || { echo "missing $f" >&2; exit 1; }
done

echo "== installing driver =="
install -m 0755 "$driver" /usr/local/bin/goodix-touch-driver.py
install -m 0644 "$unit" /etc/systemd/system/goodix-touch.service

echo "== switching services (old shim disabled, not removed) =="
systemctl daemon-reload
systemctl disable --now touch-filter.service 2>/dev/null || true
systemctl enable goodix-touch.service
# 'enable --now' would not restart an already-running unit, leaving the old
# process in place while the file on disk changed.
systemctl restart goodix-touch.service

# Give it a moment to create the virtual device and take the grab.
sleep 2

echo "== status =="
state=$(systemctl is-active goodix-touch.service 2>/dev/null || true)
enabled=$(systemctl is-enabled goodix-touch.service 2>/dev/null || true)
echo "active:  $state"
echo "enabled: $enabled"

if [ "$state" != active ] || [ "$enabled" != enabled ]; then
    echo
    echo "service did not come up - rolling back to the old shim" >&2
    "$here/rollback.sh" || true
    exit 1
fi

if ! "$here/verify.sh"; then
    echo
    echo "verification FAILED - rolling back to the old shim" >&2
    "$here/rollback.sh" || true
    exit 1
fi

echo
echo "installed.  Tap the screen a few times, then run:"
echo "    sudo journalctl -u goodix-touch.service -n 30 --no-pager"
