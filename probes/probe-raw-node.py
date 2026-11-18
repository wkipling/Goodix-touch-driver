#!/usr/bin/env python3
"""Read a raw hidraw node for N seconds and log length + hex with timestamps.

    python3 probe-raw-node.py /dev/hidraw2 600 > /tmp/hidraw2.log

Read-only and non-exclusive.  The report ID is data[0] for these devices.
"""
import os
import select
import sys
import time

path = sys.argv[1] if len(sys.argv) > 1 else "/dev/hidraw2"
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0

print("node %s for %.0fs" % (path, seconds), flush=True)
fd = os.open(path, os.O_RDONLY)
start = time.time()
end = start + seconds
try:
    while time.time() < end:
        ready, _, _ = select.select([fd], [], [], 0.5)
        if not ready:
            continue
        data = os.read(fd, 256)
        if data:
            print("%9.3f %3d %s"
                  % (time.time() - start, len(data), data.hex()), flush=True)
finally:
    os.close(fd)
