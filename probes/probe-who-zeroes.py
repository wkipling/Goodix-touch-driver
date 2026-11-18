#!/usr/bin/env python3
"""Create a probe device and report who has it open when its fuzz changes."""
import fcntl
import os
import struct
import time

from evdev import AbsInfo, UInput, ecodes as e

FUZZ = 80


def get_abs(fd, code):
    buf = bytearray(24)
    fcntl.ioctl(fd, (2 << 30) | (24 << 16) | (0x45 << 8) | (0x40 + code), buf)
    return struct.unpack("iiiiii", bytes(buf))


def openers(path):
    found = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        fd_dir = "/proc/%s/fd" % pid
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target == path:
                try:
                    with open("/proc/%s/cmdline" % pid, "rb") as f:
                        cmd = f.read().replace(b"\0", b" ").decode(
                            "utf-8", "replace").strip()
                except OSError:
                    cmd = "?"
                found.append((pid, cmd))
                break
    return found


caps = {
    e.EV_ABS: [
        (e.ABS_MT_SLOT, AbsInfo(0, 0, 9, 0, 0, 0)),
        (e.ABS_MT_TRACKING_ID, AbsInfo(0, 0, 65535, 0, 0, 0)),
        (e.ABS_MT_POSITION_X, AbsInfo(0, 0, 16383, FUZZ, 0, 0)),
        (e.ABS_MT_POSITION_Y, AbsInfo(0, 0, 9599, FUZZ, 0, 0)),
        (e.ABS_X, AbsInfo(0, 0, 16383, FUZZ, 0, 0)),
        (e.ABS_Y, AbsInfo(0, 0, 9599, FUZZ, 0, 0)),
    ],
    e.EV_KEY: [e.BTN_TOUCH],
}

ui = UInput(caps, name="FuzzProbe", input_props=[e.INPUT_PROP_DIRECT])
try:
    path = ui.device.path
    print("device", path, flush=True)
    start = time.time()
    reported = False
    for delay in (0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.8):
        while time.time() - start < delay:
            time.sleep(0.005)
        fd = os.open(path, os.O_RDONLY)
        try:
            fuzz = get_abs(fd, e.ABS_X)[3]
        finally:
            os.close(fd)
        print("%4.2fs fuzz=%d openers=%s"
              % (time.time() - start, fuzz, openers(path)), flush=True)
        if fuzz == 0 and not reported:
            reported = True
finally:
    ui.close()
