#!/bin/sh
# Discriminator: does this firmware honour InputMode at all?
#
# InputMode=1 was accepted by the device (usbmon shows the SET_REPORT completed
# with status 0) and changed nothing about the digitizer stream.  That is either
# "the mode does not affect the multitouch report" or "the firmware ignores the
# setting".  Mouse mode separates the two: interface 2 declares a three-button
# wheel mouse (report 0x07) and is silent while the panel is in touchscreen
# mode, so if it wakes up under InputMode=0 the setting is being honoured.
#
# Touch is expected to stop working on the page for the length of the run, and
# InputMode=2 is restored on every exit path.
set -u

SECS="${1:-40}"
HERE="$(cd "$(dirname "$0")" && pwd)"

restore() {
    echo "restoring InputMode=2"
    python3 "$HERE/probe-input-mode.py" /dev/hidraw0 2 ||
        echo "RESTORE FAILED - reset the panel"
}
trap restore EXIT INT TERM

echo "setting InputMode=0 (mouse)"
python3 "$HERE/probe-input-mode.py" /dev/hidraw0 0 --keep || exit 1

echo "watching hidraw0 and hidraw2 for ${SECS}s"
python3 "$HERE/probe-mouse-node.py" "$SECS"
echo "done"
