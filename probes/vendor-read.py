#!/usr/bin/env python3
"""Try a read-only memory-read packet on the vendor interface (.0002).

The interface is 63-byte output report 0x02 / input report 0x01, silent until
commanded.  Goodix's open-source GTX5 protocol reads memory with:

    [report_id, 0x20, 0, 0, 5, 1, addr_hi, addr_lo, len_hi, len_lo]
    response: [report_id, ?, ?, pkg_index, len, data...]

This sends that framing with report IDs 0x02/0x01, read flag only.  It does
not send a write/update command.  If the device does not answer, stop.

    vendor-read.py [address] [length]
"""
import os
import select
import sys
import time

PATH = "/dev/hidraw1"
OUT_ID = 0x02
IN_ID = 0x01
REPORT_LEN = 64          # report id + 63-byte payload

addr = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x8240
length = int(sys.argv[2], 0) if len(sys.argv) > 2 else 12

pkt = bytearray(REPORT_LEN)
pkt[0] = OUT_ID
pkt[1] = 0x20            # _I2C_DIRECT_RW
pkt[2] = 0x00
pkt[3] = 0x00
pkt[4] = 0x05
pkt[5] = 0x01            # read
pkt[6] = (addr >> 8) & 0xFF
pkt[7] = addr & 0xFF
pkt[8] = (length >> 8) & 0xFF
pkt[9] = length & 0xFF

fd = os.open(PATH, os.O_RDWR)
try:
    print("write %s: %s" % (PATH, " ".join("%02x" % b for b in pkt[:16])),
          flush=True)
    t0 = time.time()
    try:
        wrote = os.write(fd, pkt)
        print("wrote %d bytes in %.1f ms" % (wrote, (time.time() - t0) * 1000),
              flush=True)
    except OSError as exc:
        print("write failed: %r" % (exc,), flush=True)
        sys.exit(1)

    ready, _, _ = select.select([fd], [], [], 1.0)
    if not ready:
        print("no response within 1s", flush=True)
        sys.exit(2)
    data = os.read(fd, REPORT_LEN)
    print("read %d bytes: %s"
          % (len(data), " ".join("%02x" % b for b in data)), flush=True)
    if data and data[0] == IN_ID:
        print("input report id ok; payload len byte [4]=%d" % data[4],
              flush=True)
finally:
    os.close(fd)
