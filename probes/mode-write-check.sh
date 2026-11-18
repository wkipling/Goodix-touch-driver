#!/bin/sh
# Confirm the host really sends the InputMode SET_REPORT feature write.
#
# The panel answers HIDIOCGFEATURE with the last input report, so the mode
# cannot be read back.  This script watches the control pipe instead: usbmon
# records the setup stage of every control transfer, so a
# bmRequestType=0x21 bRequest=0x09 wValue=0x0321 write is direct evidence that
# the host sent it and whether the device completed it with status 0.
#
# Usage: mode-write-check.sh [MODE]
set -u

MODE="${1:-1}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MON=/sys/kernel/debug/usb/usbmon/3u

if [ ! -r "$MON" ]; then
    echo "usbmon not readable; mount debugfs or modprobe usbmon" >&2
    exit 1
fi

cat "$MON" > /tmp/usbmon.txt &
MONPID=$!
sleep 1

# Writes MODE, then restores 2 on exit (the read-back is an input echo, so the
# script falls back to the documented default).
python3 "$HERE/probe-input-mode.py" /dev/hidraw0 "$MODE"
sleep 1
kill "$MONPID" 2>/dev/null
wait "$MONPID" 2>/dev/null

echo "--- control transfers touching feature report 0x21:"
grep -E " 21 09 0321 " /tmp/usbmon.txt | sed 's/^[0-9a-f]* //' | tail -10
echo "--- completions of those transfers:"
grep -E "^[0-9a-f]+ [0-9]+ C .* 21 " /tmp/usbmon.txt | tail -10
echo "--- captured $(wc -l < /tmp/usbmon.txt) usbmon lines"
