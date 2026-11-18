#!/usr/bin/env python3
"""Verify that absinfo fuzz set at UInput creation survives to the event node.

The driver notes claim this cannot be done (EVIOCSABS is a no-op on uinput and
a fresh open still reports fuzz=0).  That is true of EVIOCSABS.  It is not the
right interface: UI_DEV_SETUP plus UI_ABS_SETUP must be used *before* the
device is created.  python-evdev does that when absinfo is passed to UInput().

This creates a throwaway virtual device, reads its absinfo through a *fresh*
open (the authoritative way to check), then destroys it.
"""
import fcntl
import os
import struct

from evdev import AbsInfo, UInput, ecodes as e

X_MAX, Y_MAX = 16383, 9599
FUZZ = 80


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
    print("created %s" % path)
    fd = os.open(path, os.O_RDONLY)
    try:
        ok = True
        for code in (e.ABS_X, e.ABS_Y, e.ABS_MT_POSITION_X, e.ABS_MT_POSITION_Y):
            value, minimum, maximum, fuzz, flat, res = get_abs(fd, code)
            print("%-22s value=%d min=%d max=%d fuzz=%d flat=%d res=%d"
                  % (e.ABS[code], value, minimum, maximum, fuzz, flat, res))
            ok = ok and fuzz == FUZZ
        print("RESULT:", "fuzz survives" if ok else "fuzz LOST")
    finally:
        os.close(fd)
finally:
    ui.close()
