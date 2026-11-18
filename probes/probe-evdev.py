#!/usr/bin/env python3
"""Read an evdev node for N seconds and print a timestamped event log.

Read-only: does not grab.  Can run alongside X and the driver.

    python3 probe-evdev.py /dev/input/event14 600 > /tmp/goodix-evdev.log
"""
import select
import sys
import time

from evdev import InputDevice, ecodes

path = sys.argv[1] if len(sys.argv) > 1 else "/dev/input/event14"
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0

dev = InputDevice(path)
print("device %s name=%r" % (dev.path, dev.name), flush=True)
start = time.time()
end = start + seconds
try:
    while time.time() < end:
        ready, _, _ = select.select([dev.fd], [], [], 0.5)
        if not ready:
            continue
        for ev in dev.read():
            name = ecodes.bytype.get(ev.type, {}).get(ev.code, ev.code)
            print("%8.3f  %-18s %-22s %d"
                  % (time.time() - start, ecodes.EV.get(ev.type, ev.type),
                     name, ev.value), flush=True)
finally:
    dev.close()
