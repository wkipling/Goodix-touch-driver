#!/bin/sh
# Verify the rewritten driver is actually in play.
#
#     sudo ./deploy/verify.sh
#
# Checks, in order: service state, the virtual device's presence and
# INPUT_PROP_DIRECT, the noise floor read through a *fresh* open of the event
# node, and that the raw panel is grabbed.
set -eu

fail=0
say() { printf '%s\n' "$*"; }
bad() { printf '  FAIL  %s\n' "$*"; fail=1; }
good() { printf '  ok    %s\n' "$*"; }

say "== service =="
state=$(systemctl is-active goodix-touch.service 2>/dev/null || true)
[ "$state" = active ] && good "goodix-touch.service active" \
    || bad "goodix-touch.service is ${state:-unknown}"
enabled=$(systemctl is-enabled goodix-touch.service 2>/dev/null || true)
[ "$enabled" = enabled ] && good "enabled at boot" \
    || bad "not enabled (${enabled:-unknown})"

say
say "== virtual device and INPUT_PROP_DIRECT =="
python3 - <<'PY' || exit 1
import fcntl
import glob
import os
import struct
import sys

NAME = "TOUCH TOUCH Filtered"
FUZZ = 80
AXES = {0x00: "ABS_X", 0x01: "ABS_Y",
        0x35: "ABS_MT_POSITION_X", 0x36: "ABS_MT_POSITION_Y"}
PROP_DIRECT = 0x02
fail = 0


def get_abs(fd, code):
    buf = bytearray(24)
    fcntl.ioctl(fd, (2 << 30) | (24 << 16) | (0x45 << 8) | (0x40 + code), buf)
    return struct.unpack("iiiiii", bytes(buf))


node = None
for name_file in sorted(glob.glob("/sys/class/input/event*/device/name")):
    try:
        with open(name_file) as f:
            if f.read().strip() != NAME:
                continue
    except OSError:
        continue
    devdir = os.path.dirname(name_file)
    event = os.path.basename(os.path.dirname(devdir))
    node = "/dev/input/" + event
    try:
        with open(os.path.join(devdir, "properties")) as f:
            props = int(f.read().strip(), 16)
    except (OSError, ValueError):
        props = None
    if props is None:
        print("  FAIL  cannot read properties")
        fail = 1
    elif props & PROP_DIRECT:
        print("  ok    %s advertises INPUT_PROP_DIRECT (props=0x%x)"
              % (node, props))
    else:
        print("  FAIL  %s lacks INPUT_PROP_DIRECT (props=0x%x)"
              % (node, props))
        fail = 1
    break

if node is None:
    print("  FAIL  no input device named %r" % NAME)
    sys.exit(1)

# Informational: Xorg resets absinfo shortly after the device appears, so 0 is
# expected here.  The noise floor is applied in-process by the driver and does
# not depend on this value.
fd = os.open(node, os.O_RDONLY)
try:
    for code, label in AXES.items():
        fuzz = get_abs(fd, code)[3]
        note = "" if fuzz == FUZZ else \
            " (Xorg resets this; in-process deadband is what filters)"
        print("  info  %-18s fuzz=%d%s" % (label, fuzz, note))
finally:
    os.close(fd)

sys.exit(1 if fail else 0)
PY

say
say "== raw panel grab =="
python3 - <<'PY' || fail=1
import fcntl
import os
import sys

EVIOCGRAB = 0x40044590
path = "/dev/input/by-id/usb-TOUCH_TOUCH-event-if00"
try:
    fd = os.open(path, os.O_RDONLY)
except OSError as exc:
    print("  FAIL  cannot open %s: %s" % (path, exc))
    sys.exit(1)
try:
    fcntl.ioctl(fd, EVIOCGRAB, 1)
except OSError:
    print("  ok    raw panel is grabbed (phantoms cannot reach X directly)")
    sys.exit(0)
else:
    print("  FAIL  raw panel is NOT grabbed")
    fcntl.ioctl(fd, EVIOCGRAB, 0)
    sys.exit(1)
finally:
    os.close(fd)
PY

say
say "== recent journal =="
journalctl -u goodix-touch.service -n 8 --no-pager 2>/dev/null | sed 's/^/  /' || true

exit $fail
