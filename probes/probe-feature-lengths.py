#!/usr/bin/env python3
"""Try HIDIOCGFEATURE with several buffer lengths for report 0x21."""
import fcntl
import os
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/dev/hidraw0"
report = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x21
fd = os.open(path, os.O_RDWR)
try:
    for length in range(2, 9):
        buf = bytearray(length)
        buf[0] = report
        req = (3 << 30) | (length << 16) | (ord("H") << 8) | 0x07
        try:
            fcntl.ioctl(fd, req, buf)
            print("len=%d ok  %s" % (length, " ".join("%02x" % b for b in buf)))
        except OSError as exc:
            print("len=%d err %s" % (length, exc))
finally:
    os.close(fd)
