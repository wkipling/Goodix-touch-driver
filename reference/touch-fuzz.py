#!/usr/bin/env python3
"""Set a noise floor (fuzz) on the touch panel's axes.

The Goodix 27c6:8191 panel declares fuzz=0 on every axis, so the input core
passes on every single-unit wobble it emits. Held still, a finger drifts
12-22 CSS px, which is past Chromium's 8 px touch slop: the tap becomes a
scroll and is cancelled, so a tapped button only sometimes activates.

fuzz tells the input core how much movement to treat as noise, and it is
applied before libinput or Chromium ever see the events. 80 raw units is
about 7.5 CSS px in X and 8 in Y: enough to swallow the drift, far below any
deliberate movement.

Debian's udev rules do not apply EVDEV_ABS_* (60-evdev.rules only reads
hwdb), so the setting is applied directly here and re-applied at boot by
touch-fuzz.service. The setting lives in the kernel per device and does not
survive re-registration, which is why this is a service and not a one-off.
"""

import fcntl
import glob
import os
import struct
import sys
import time

VENDOR = 0x27C6
PRODUCT = 0x8191
FUZZ = 80

# struct input_absinfo is six ints.
EVIOCGABS = lambda code: (2 << 30) | (24 << 16) | (0x45 << 8) | (0x40 + code)
EVIOCSABS = lambda code: (1 << 30) | (24 << 16) | (0x45 << 8) | (0xC0 + code)

ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT = 0x2F
ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 0x35, 0x36
AXES = (ABS_X, ABS_Y, ABS_MT_POSITION_X, ABS_MT_POSITION_Y)


def log(msg):
    print(time.strftime("%F %T"), msg, flush=True)


def read_uint(path, name):
    try:
        with open(os.path.join(path, name)) as f:
            return int(f.read().strip(), 16)
    except (OSError, ValueError):
        return None


def has_abs(path, code):
    """Is this axis present in the device's capability bitmap?"""
    try:
        with open(os.path.join(path, "capabilities", "abs")) as f:
            mask = int(f.read().strip(), 16)
    except (OSError, ValueError):
        return False
    return bool(mask >> code & 1)


def find_panel():
    """The multitouch interface, not the stylus/mouse/keyboard ones.

    Several of the panel's interfaces are all named "TOUCH TOUCH", so the
    name alone is not enough - only the touchscreen advertises ABS_MT_SLOT.
    """
    for path in sorted(glob.glob("/sys/class/input/event*/device")):
        # Skip virtual devices - the filter's own uinput touchscreen copies the
        # panel's vendor/product and advertises ABS_MT_SLOT too, so it would
        # otherwise look identical to the real panel.
        if "/virtual/" in os.path.realpath(path):
            continue
        if read_uint(path, "id/vendor") != VENDOR or read_uint(path, "id/product") != PRODUCT:
            continue
        if not has_abs(path, ABS_MT_SLOT):
            continue
        return os.path.join("/dev/input", os.path.basename(os.path.dirname(path)))
    return None


def get_abs(fd, code):
    buf = bytearray(24)
    fcntl.ioctl(fd, EVIOCGABS(code), buf)
    return struct.unpack("iiiiii", bytes(buf))


def set_fuzz(dev_path, fuzz):
    fd = os.open(dev_path, os.O_RDONLY)
    try:
        changed = []
        for code in AXES:
            value, minimum, maximum, cur_fuzz, flat, res = get_abs(fd, code)
            if cur_fuzz == fuzz:
                continue
            buf = struct.pack("iiiiii", value, minimum, maximum, fuzz, flat, res)
            fcntl.ioctl(fd, EVIOCSABS(code), buf)
            changed.append(code)
        return changed
    finally:
        os.close(fd)


def main():
    # The device may not exist yet at boot; wait for it.
    dev = None
    for _ in range(40):
        dev = find_panel()
        if dev:
            break
        time.sleep(0.5)
    if not dev:
        log("touch panel not found; nothing to do")
        return 1

    changed = set_fuzz(dev, FUZZ)
    fd = os.open(dev, os.O_RDONLY)
    try:
        now = {code: get_abs(fd, code)[3] for code in AXES}
    finally:
        os.close(fd)

    log("%s: fuzz set to %d on axes %s; now %s"
        % (dev, FUZZ, changed or "(already set)", now))
    if any(v != FUZZ for v in now.values()):
        log("WARNING: not every axis took the fuzz setting")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
