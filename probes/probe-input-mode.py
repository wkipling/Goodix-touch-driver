#!/usr/bin/env python3
"""Read (and optionally write) the panel's HID Input Mode feature report.

Usage:
    probe-input-mode.py [/dev/hidraw0] [new_mode] [--keep] [--index N]
                        [--restore-to MODE[,INDEX]]

Report 0x21 on the digitizer interface is:
    Usage 0x52 Input Mode, Usage 0x53 Device Index, two 8-bit values,
    logical range 0..10.

The stock driver picks InputMode=2 (touchscreen) with DeviceIndex=0.  Values 0
(mouse) and 1 (single touch) select different reporting paths inside the
controller, and are the only device-level way to ask the firmware to stop
running the slot allocator that produces the latch.  See
docs/FIRMWARE-LATCH.md section 8.

Writing changes the controller's mode.

Read-back does not work on this panel.  HIDIOCGFEATURE on hidraw returns the
last *input* report (`0d 10 d8 ...`) instead of the feature value, so the
current mode cannot be read.  This script therefore does not trust what it
reads: it restores the documented default (2, 0) unless --restore-to says
otherwise.  Confirm a write by what the input devices do, or by usbmon.

Safety:
    - the mode is restored on every exit path, including failure;
    - --keep leaves the new mode in place and prints the restore command;
    - if the panel goes silent, restore InputMode=2 with this script, or use
      the recovery steps below.

Recovery, in order:
    sudo python3 probes/probe-input-mode.py /dev/hidraw0 2
    sudo sh -c 'echo 0 > /sys/bus/usb/devices/3-2.3/authorized; \
                echo 1 > /sys/bus/usb/devices/3-2.3/authorized'
    # or unplug and replug the panel.
"""
import fcntl
import os
import sys

REPORT_ID = 0x21
LEN = 3                      # report id + two 8-bit values
DEFAULT_RESTORE = (2, 0)     # what hid-multitouch sets at bind


def _ioc(direction, typ, nr, size):
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


HIDIOCGFEATURE = _ioc(3, "H", 0x07, LEN)
HIDIOCSFEATURE = _ioc(3, "H", 0x06, LEN)


def get(fd):
    """Return the raw feature read-back, which may be an input echo."""
    buf = bytearray([REPORT_ID, 0, 0])
    fcntl.ioctl(fd, HIDIOCGFEATURE, buf)
    return list(buf)


def trustworthy(values):
    """True only if the read-back looks like feature 0x21 and not an echo."""
    return (values[0] == REPORT_ID
            and 0 <= values[1] <= 10
            and 0 <= values[2] <= 10)


def set_feature(fd, input_mode, device_index):
    buf = bytearray([REPORT_ID, input_mode & 0xFF, device_index & 0xFF])
    fcntl.ioctl(fd, HIDIOCSFEATURE, buf)


def parse_args(argv):
    keep = "--keep" in argv
    index = None
    restore = DEFAULT_RESTORE

    if "--index" in argv:
        pos = argv.index("--index")
        index = int(argv[pos + 1], 0)
        del argv[pos:pos + 2]
    if "--restore-to" in argv:
        pos = argv.index("--restore-to")
        parts = argv[pos + 1].split(",")
        restore = (int(parts[0], 0), int(parts[1], 0) if len(parts) > 1 else 0)
        del argv[pos:pos + 2]

    args = [a for a in argv if not a.startswith("--")]
    path = args[0] if args else "/dev/hidraw0"
    mode = int(args[1], 0) if len(args) > 1 else None
    return path, mode, keep, index, restore


def main():
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(__doc__)
        return 0

    path, mode, keep, index, restore = parse_args(sys.argv[1:])
    if mode is not None and not 0 <= mode <= 10:
        print("mode must be 0..10")
        return 2

    fd = os.open(path, os.O_RDWR)
    changed = False
    try:
        before = get(fd)
        if trustworthy(before):
            print("%s feature 0x%02X reads input_mode=%d device_index=%d"
                  % (path, before[0], before[1], before[2]))
            if "--restore-to" not in sys.argv:
                restore = (before[1], before[2])
        else:
            print("%s read-back is an input echo (report 0x%02X, values %d/%d);"
                  " current mode unknown, will restore %d/%d"
                  % (path, before[0], before[1], before[2],
                     restore[0], restore[1]))

        if mode is None:
            return 0

        device_index = restore[1] if index is None else index
        print("writing input_mode=%d device_index=%d" % (mode, device_index))
        set_feature(fd, mode, device_index)
        changed = True
        return 0
    finally:
        if changed:
            if keep:
                print("keeping mode %d; restore with: %s %s %d --index %d"
                      % (mode, sys.argv[0], path, restore[0], restore[1]))
            else:
                try:
                    set_feature(fd, restore[0], restore[1])
                    print("restored input_mode=%d device_index=%d"
                          % (restore[0], restore[1]))
                except OSError as exc:
                    print("RESTORE FAILED (%s) - reset the panel: see recovery"
                          " in this file" % exc)
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
