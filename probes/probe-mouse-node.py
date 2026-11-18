#!/usr/bin/env python3
"""Watch the digitizer and the vendor mouse node at the same time.

Usage:
    probe-mouse-node.py [SECONDS]

Prints, per node, how many bytes arrived and which report IDs were seen, plus
the first raw frames.  It exists to test whether an InputMode change actually
re-routes the controller: in mouse mode the digitizer may fall silent while
interface 2 (report 0x07, three buttons and a wheel) wakes up.  If nothing
changes on either node, the firmware is ignoring InputMode.
"""
import os
import select
import sys
import time

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 40.0
NODES = ("/dev/hidraw0", "/dev/hidraw2")

stats = {n: {"bytes": 0, "frames": 0, "ids": {}} for n in NODES}
samples = {n: [] for n in NODES}

fds = {}
for node in NODES:
    try:
        fds[os.open(node, os.O_RDONLY)] = node
    except OSError as exc:
        print("%s: %s" % (node, exc))

end = time.time() + SECONDS
while time.time() < end and fds:
    ready, _, _ = select.select(list(fds), [], [], 0.5)
    for fd in ready:
        node = fds[fd]
        data = os.read(fd, 4096)
        if not data:
            continue
        stats[node]["bytes"] += len(data)
        stats[node]["frames"] += 1
        rid = data[0]
        stats[node]["ids"][rid] = stats[node]["ids"].get(rid, 0) + 1
        if len(samples[node]) < 3:
            samples[node].append(data[:24].hex())

print()
for node in NODES:
    s = stats[node]
    ids = ", ".join("0x%02X x%d" % (k, v) for k, v in sorted(s["ids"].items()))
    print("%s: %d frames, %d bytes" % (node, s["frames"], s["bytes"]))
    print("   report ids: %s" % (ids or "none"))
    for hexframe in samples[node]:
        print("   frame: %s" % hexframe)
