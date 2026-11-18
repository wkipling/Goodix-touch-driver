#!/usr/bin/env python3
"""Read or write Contact Count Maximum (feature report 0x0A).

Usage:
    probe-contact-max.py [/dev/hidraw0] [new_max] [--keep]

Report 0x0A is two bytes: report id 0x0A, then one 8-bit value with logical
range 0..10.  The stock value is 10, matching the ten finger blocks in report
0x0D.

Setting it to 1 asks the controller to track a single contact.  Unlike the
InputMode feature, this one describes how many contacts the firmware should
maintain, so if the firmware honours it there is no second slot for a phantom
to occupy.  Whether it is honoured is the whole point of the test.

Reading is unreliable: this panel can answer the feature read with its last
input report, so a read-back that does not look like `0a <n>` is treated as an
echo and the restore value falls back to 10.

Safety: the previous value is restored on every exit path; --keep leaves the
new value and prints the restore command.
"""
import fcntl
import os
import sys

REPORT_ID = 0x0A
LEN = 2
DEFAULT_RESTORE = 10


def _ioc(direction, typ, nr, size):
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


HIDIOCGFEATURE = _ioc(3, "H", 0x07, LEN)
HIDIOCSFEATURE = _ioc(3, "H", 0x06, LEN)


def get(fd):
    buf = bytearray([REPORT_ID, 0])
    fcntl.ioctl(fd, HIDIOCGFEATURE, buf)
    return list(buf)


def plausible(values):
    return values[0] == REPORT_ID and 0 <= values[1] <= 10


def set_feature(fd, value):
    buf = bytearray([REPORT_ID, value & 0xFF])
    fcntl.ioctl(fd, HIDIOCSFEATURE, buf)


def main():
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        return 0
    keep = "--keep" in args
    args = [a for a in args if not a.startswith("--")]
    path = args[0] if args else "/dev/hidraw0"
    value = int(args[1], 0) if len(args) > 1 else None
    if value is not None and not 0 <= value <= 10:
        print("value must be 0..10")
        return 2

    fd = os.open(path, os.O_RDWR)
    restore = DEFAULT_RESTORE
    changed = False
    try:
        before = get(fd)
        if plausible(before):
            print("%s contact maximum reads %d" % (path, before[1]))
            restore = before[1]
        else:
            print("%s read-back is an echo (0x%02X); assuming %d"
                  % (path, before[0], restore))
        if value is None:
            return 0

        print("writing contact maximum = %d" % value)
        set_feature(fd, value)
        changed = True
        return 0
    finally:
        if changed:
            if keep:
                print("keeping %d; restore with: %s %s %d"
                      % (value, sys.argv[0], path, restore))
            else:
                try:
                    set_feature(fd, restore)
                    print("restored contact maximum = %d" % restore)
                except OSError as exc:
                    print("RESTORE FAILED (%s) - replug the panel" % exc)
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
