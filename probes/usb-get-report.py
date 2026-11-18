#!/usr/bin/env python3
"""Ask the HID device for a feature report directly over USB (usbfs).

Bypasses hidraw/kernel HID parsing.  Read-only: GET_REPORT only.

    usb-get-report.py 0x0A 1
    usb-get-report.py 0x21 2
"""
import ctypes
import fcntl
import os
import struct
import sys

USBDEVFS_CONTROL = (3 << 30) | (24 << 16) | (ord("U") << 8) | 0
GET_REPORT = 0x01
FEATURE = 0x03


def find_device(vid="27c6", pid="8191"):
    base = "/sys/bus/usb/devices"
    for name in os.listdir(base):
        path = os.path.join(base, name)
        try:
            with open(os.path.join(path, "idVendor")) as f:
                v = f.read().strip()
            with open(os.path.join(path, "idProduct")) as f:
                p = f.read().strip()
            if v.lower() != vid.lower() or p.lower() != pid.lower():
                continue
            with open(os.path.join(path, "busnum")) as f:
                bus = int(f.read().strip())
            with open(os.path.join(path, "devnum")) as f:
                dev = int(f.read().strip())
            return "/dev/bus/usb/%03d/%03d" % (bus, dev), name
        except (OSError, ValueError):
            continue
    return None, None


def get_report(fd, report_id, length, interface=0):
    data = ctypes.create_string_buffer(length)
    ctrl = struct.pack("=BBHHHI4xQ",
                       0xA1, GET_REPORT,
                       (FEATURE << 8) | report_id,
                       interface, length, 1000,
                       ctypes.addressof(data))
    fcntl.ioctl(fd, USBDEVFS_CONTROL, bytearray(ctrl))
    return bytes(data.raw[:length])


def main():
    report_id = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x0A
    length = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    path, name = find_device()
    if path is None:
        print("device not found")
        return 1
    print("device %s (%s)" % (path, name))
    fd = os.open(path, os.O_RDWR)
    try:
        try:
            data = get_report(fd, report_id, length)
            print("GET feature 0x%02X len %d -> %s"
                  % (report_id, length, " ".join("%02x" % b for b in data)))
        except OSError as exc:
            print("GET feature 0x%02X len %d failed: %s"
                  % (report_id, length, exc))
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
