#!/usr/bin/env python3
"""Read report 0x0D from hidraw0 and record what the firmware actually sends.

Read-only and non-exclusive: the running filter may keep its own descriptor
open; hidraw delivers a copy to every reader.  The panel is change-driven, so
there is nothing to record unless someone touches it.
"""
import collections
import json
import os
import select
import sys
import time

PATH = "/dev/hidraw0"
REPORT_ID = 0x0D
FINGERS = 10
SCAN_TIME_OFFSET = 50
CONTACT_COUNT_OFFSET = 52
REPORT_LEN = 53
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
OUT = "/tmp/goodix-probe.jsonl"

ids = collections.Counter()
tipped_counts = collections.Counter()
counts = collections.Counter()
lengths = collections.Counter()
reports = 0
with_id1 = 0
solo_not_id1 = 0
start = time.time()
end = start + SECONDS
with open(OUT, "w") as out:
    fd = os.open(PATH, os.O_RDONLY)
    try:
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.5)
            if not r:
                continue
            data = os.read(fd, 64)
            if not data:
                continue
            lengths[len(data)] += 1
            if data[0] != REPORT_ID:
                continue
            payload = data[1:]
            if len(payload) < REPORT_LEN:
                continue
            reports += 1
            found = []
            for n in range(FINGERS):
                b0 = payload[5 * n]
                if b0 & 0x01:
                    found.append(((b0 >> 4) & 0x0F,
                                  payload[5 * n + 1] | (payload[5 * n + 2] << 8),
                                  payload[5 * n + 3] | (payload[5 * n + 4] << 8)))
            count = payload[CONTACT_COUNT_OFFSET]
            scan = payload[SCAN_TIME_OFFSET] | (payload[SCAN_TIME_OFFSET + 1] << 8)
            tipped_counts[len(found)] += 1
            counts[count] += 1
            for cid, x, y in found:
                ids[cid] += 1
            if any(c[0] == 1 for c in found):
                with_id1 += 1
            if len(found) == 1 and found[0][0] != 1:
                solo_not_id1 += 1
            out.write(json.dumps({
                "t": round(time.time() - start, 3),
                "len": len(data),
                "scan": scan,
                "count": count,
                "contacts": found,
            }) + "\n")
            out.flush()
    finally:
        os.close(fd)

summary = {
    "seconds": SECONDS,
    "reports": reports,
    "wire_lengths": dict(lengths),
    "tipped_per_report": dict(sorted(tipped_counts.items())),
    "contact_count_values": dict(sorted(counts.items())),
    "ids_seen": dict(sorted(ids.items())),
    "reports_with_id1": with_id1,
    "solo_reports_not_id1": solo_not_id1,
}
print(json.dumps(summary, indent=2))
with open("/tmp/goodix-probe-summary.json", "w") as f:
    json.dump(summary, f, indent=2)
