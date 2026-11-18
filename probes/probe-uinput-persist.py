#!/usr/bin/env python3
"""Watch a freshly created uinput device's absinfo fuzz over time.

Creates a throwaway device named FuzzProbe with absinfo fuzz=80, then reads
it back through a fresh open every 250ms.  If the value changes, something on
the system is rewriting absinfo; if it stays, the failed deployment check was
looking at the wrong event node.
"""
import fcntl
import os
import struct
import time

from evdev import AbsInfo, UInput, ecodes as e

FUZZ = 80
X_MAX, Y_MAX = 16383, 9599


def get_abs(fd, code):
    buf = bytearray(24)
    fcntl.ioctl(fd, (2 << 30) | (24 << 16) | (0x45 << 8) | (0x40 + code), buf)
    return struct.unpack("iiiiii", bytes(buf))


caps = {
    e.EV_ABS: [
        (e.ABS_MT_SLOT, AbsInfo(0, 0, 9, 0, 0, 0)),
        (e.ABS_MT_TRACKING_ID, AbsInfo(0, 0, 65535, 0, 0, 0)),
        (e.ABS_MT_POSITION_X, AbsInfo(0, 0, X_MAX, FUZZ, 0, 0)),
        (e.ABS_MT_POSITION_Y, AbsInfo(0, 0, Y_MAX, FUZZ, 0, 0)),
        (e.ABS_X, AbsInfo(0, 0, X_MAX, FUZZ, 0, 0)),
        (e.ABS_Y, AbsInfo(0, 0, Y_MAX, FUZZ, 0, 0)),
    ],
    e.EV_KEY: [e.BTN_TOUCH],
}

ui = UInput(caps, name="FuzzProbe", input_props=[e.INPUT_PROP_DIRECT])
try:
    path = ui.device.path
    print("device", path)
    for i in range(24):
        fd = os.open(path, os.O_RDONLY)
        try:
            vals = [get_abs(fd, c)[3] for c in
                    (e.ABS_X, e.ABS_Y, e.ABS_MT_POSITION_X,
                     e.ABS_MT_POSITION_Y)]
        finally:
            os.close(fd)
        print("%4.2fs  fuzz=%s  sysfs-name=%s"
              % (i * 0.25, vals,
                 open("/sys/class/input/%s/device/name"
                      % os.path.basename(path)).read().strip()))
        time.sleep(0.25)
finally:
    ui.close()
