#!/usr/bin/env python3
"""Does re-sending the controller's mode command clear a held contact?

Waits for BTN_TOUCH down on the virtual touchscreen, then after HOLD_S sends
the same SET_REPORT that hid-multitouch sends at bind:

    feature 0x21, InputMode=2, DeviceInputMode=0

and logs whether the press ends immediately (tracker reset) or only when the
finger is lifted.  Run for N seconds; hold a finger on the screen for ~5s
when it says READY.

    hold-reset-test.py [seconds]
"""
import fcntl
import glob
import os
import select
import sys
import time

from evdev import InputDevice, ecodes as e

NAME = "TOUCH TOUCH Filtered"
HOLD_S = 1.0
RUN_S = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
HIDRAW = "/dev/hidraw0"
HIDIOCSFEATURE = (3 << 30) | (3 << 16) | (ord("H") << 8) | 0x06


def find_virtual():
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            dev = InputDevice(path)
        except OSError:
            continue
        if dev.name == NAME:
            return dev
        dev.close()
    return None


def set_input_mode(fd, mode=2, device_mode=0):
    buf = bytearray([0x21, mode, device_mode])
    fcntl.ioctl(fd, HIDIOCSFEATURE, buf)


def log(msg):
    print("%8.3f %s" % (time.monotonic() - START, msg), flush=True)


START = time.monotonic()
dev = find_virtual()
if dev is None:
    print("no virtual touchscreen; is the driver running?", flush=True)
    sys.exit(1)
hid = os.open(HIDRAW, os.O_RDWR)
print("READY: hold a finger on the screen for about 5 seconds", flush=True)

down_at = None
sent_at = None
try:
    end = time.monotonic() + RUN_S
    while time.monotonic() < end:
        if down_at is not None and sent_at is None and \
                time.monotonic() - down_at >= HOLD_S:
            try:
                set_input_mode(hid)
                sent_at = time.monotonic()
                log("SET_REPORT 0x21 = 21 02 00")
            except OSError as exc:
                log("SET_REPORT failed: %r" % (exc,))
                sent_at = time.monotonic()
        ready, _, _ = select.select([dev.fd], [], [], 0.02)
        if not ready:
            continue
        for ev in dev.read():
            if ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH:
                if ev.value == 1 and down_at is None:
                    down_at = time.monotonic()
                    sent_at = None
                    log("BTN_TOUCH down")
                elif ev.value == 0 and down_at is not None:
                    held = time.monotonic() - down_at
                    after = None if sent_at is None else \
                        time.monotonic() - sent_at
                    log("BTN_TOUCH up  held %.0fms  %s"
                        % (held * 1000,
                           "no SET sent" if after is None else
                           "release %.0fms after SET" % (after * 1000)))
                    down_at = None
                    sent_at = None
finally:
    dev.close()
    os.close(hid)
