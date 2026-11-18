#!/usr/bin/env python3
"""Read (and optionally write) the panel's HID Input Mode feature report.

Usage:
    probe-input-mode.py [/dev/hidraw0] [new_mode] [--keep] [--index N]

Report 0x21 on the digitizer interface is:
    Usage 0x52 Input Mode, Usage 0x53 Device Index, two 8-bit values,
    logical range 0..10.

The stock driver picks InputMode=2 (touchscreen) with DeviceIndex=0.  Values 0
(mouse) and 1 (single touch) select different reporting paths inside the
controller, and are the only device-level way to ask the firmware to stop
running the slot allocator that produces the latch.  See
docs/FIRMWARE-LATCH.md section 8.

Reading is side-effect free.  Writing changes the controller's mode.

Safety:
    - the previous values are restored on every exit path, including failure;
    - --keep leaves the new mode in place and prints the command that restores
      it;
    - if the panel goes silent, restore InputMode=2 with this script, or reset
      the USB device (see "recovery" below).

Recovery, in order:
    sudo python3 probes/probe-input-mode.py /dev/hidraw0 2
    sudo sh -c 'echo 0 > /sys/bus/usb/devices/3-2.3/authorized; \
                echo 1 > /sys/bus/usb/devices/3-2.3/authorized'
    # or simply unplug and replug the panel.

Caveat: HIDIOCGFEATURE on hidraw can return the last *input* report rather than
the feature value (observed on this panel).  Treat the echoed value as weak
evidence and confirm the mode by what the input devices do.
"""
import fcntl
import os
import sys

args = sys.argv[1:]
keep = "--keep" in args
device_index_arg = None
if "--index" in args:
    pos = args.index("--index")
    device_index_arg = int(args[pos + 1], 0)
    del args[pos:pos + 2]
args = [a for a in args if not a.startswith("--")]

PATH = args[0] if args else "/dev/hidraw0"
mode = int(args[1], 0) if len(args) > 1 else None
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


def set_feature(fd, input_mode, device_index):
    buf = bytearray([REPORT_ID, input_mode & 0xFF, device_index & 0xFF])
    fcntl.ioctl(fd, HIDIOCSFEATURE, buf)


def main():
    if mode is not None and (mode < 0 or mode > 10):
        print("mode must be 0..10")
        return 2

    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(__doc__)
        sys.exit(0)

    fd = os.open(PATH, os.O_RDWR)
    previous = None
    changed = False
    try:
        before = get(fd)
        print("%s report 0x%02X: input_mode=%d device_index=%d"
              % (PATH, before[0], before[1], before[2]))
        if mode is None:
            return 0

        previous = (before[1], before[2])
        device_index = before[2] if device_index_arg is None else device_index_arg
        print("writing input_mode=%d device_index=%d" % (mode, device_index))
        set_feature(fd, mode, device_index)
        changed = True

        after = get(fd)
        print("read back: input_mode=%d device_index=%d (may echo input)"
              % (after[1], after[2]))
        return 0
    finally:
        if changed and previous is not None:
            if keep:
                print("keeping mode; restore with: %s %s %d"
                      % (sys.argv[0], PATH, previous[0]))
            else:
                try:
                    set_feature(fd, previous[0], previous[1])
                    print("restored input_mode=%d device_index=%d"
                          % (previous[0], previous[1]))
                except OSError as exc:
                    print("RESTORE FAILED (%s) - reset the panel: see "
                          "recovery in this file" % exc)
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
