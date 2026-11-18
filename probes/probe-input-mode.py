#!/usr/bin/env python3
"""Read (and optionally write) the panel's HID Input Mode feature report.

Usage:
    probe-input-mode.py [/dev/hidraw0] [new_mode]

Report 0x21 on the digitizer interface is:
    Usage 0x52 Input Mode, Usage 0x53 Device Input Mode, two 8-bit values,
    logical range 0..10.

Reading is side-effect free.  Writing changes the controller's mode; only the
documented values 0x00..0x03 should be tried, and the script restores the old
value unless --keep is given.
"""
import fcntl
import os
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/dev/hidraw0"
REPORT_ID = 0x21
LEN = 3                      # report id + two 8-bit values


def _ioc(direction, typ, nr, size):
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


HIDIOCGFEATURE = _ioc(3, "H", 0x07, LEN)
HIDIOCSFEATURE = _ioc(3, "H", 0x06, LEN)


def get(fd):
    buf = bytearray([REPORT_ID, 0, 0])
    fcntl.ioctl(fd, HIDIOCGFEATURE, buf)
    return list(buf)


def set_feature(fd, values):
    buf = bytearray([REPORT_ID, values[0], values[1]])
    fcntl.ioctl(fd, HIDIOCSFEATURE, buf)


fd = os.open(PATH, os.O_RDWR)
try:
    before = get(fd)
    print("%s report 0x%02X: input_mode=%d device_input_mode=%d"
          % (PATH, before[0], before[1], before[2]))
    if len(sys.argv) > 2:
        mode = int(sys.argv[2], 0)
        if mode < 0 or mode > 10:
            print("mode must be 0..10")
            sys.exit(2)
        set_feature(fd, [mode, before[2]])
        after = get(fd)
        print("after write: input_mode=%d device_input_mode=%d"
              % (after[1], after[2]))
        if "--keep" not in sys.argv:
            set_feature(fd, [before[1], before[2]])
            restored = get(fd)
            print("restored: input_mode=%d device_input_mode=%d"
                  % (restored[1], restored[2]))
finally:
    os.close(fd)
