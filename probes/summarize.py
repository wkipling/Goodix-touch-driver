#!/usr/bin/env python3
"""Summarise a tap session from the raw-HID and evdev probes.

    python3 summarize.py [/tmp/goodix-probe.jsonl] [/tmp/goodix-evdev.log]

The HID side answers the firmware questions (Contact Count, ids, bursts).
The evdev side answers the driver questions: did every press end, and how
long did it last?
"""
import collections
import json
import sys

hid_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/goodix-probe.jsonl"
ev_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/goodix-evdev.log"

print("== raw HID (what the firmware says) ==")
reports = 0
lengths = collections.Counter()
tipped = collections.Counter()
counts = collections.Counter()
ids = collections.Counter()
with_id1 = 0
solo_not1 = 0
count_matches = 0
count_zero_with_tips = 0
max_contacts = 0
try:
    with open(hid_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            reports += 1
            length = r.get("len")
            lengths[length] += 1
            contacts = r.get("contacts") or []
            n = len(contacts)
            tipped[n] += 1
            max_contacts = max(max_contacts, n)
            c = r.get("count")
            counts[c] += 1
            for cid, _x, _y in contacts:
                ids[cid] += 1
            if any(cid == 1 for cid, _x, _y in contacts):
                with_id1 += 1
            if n == 1 and contacts[0][0] != 1:
                solo_not1 += 1
            if c is not None and c == n:
                count_matches += 1
            if c == 0 and n:
                count_zero_with_tips += 1
except FileNotFoundError:
    print("  (no HID capture)")
    reports = 0

if reports:
    print("  reports:                 %d" % reports)
    print("  wire lengths:            %s" % dict(sorted(lengths.items())))
    print("  tipped blocks/report:    %s" % dict(sorted(tipped.items())))
    print("  max tipped in a report:  %d" % max_contacts)
    print("  ids seen:                %s" % dict(sorted(ids.items())))
    print("  reports containing id 1: %d/%d" % (with_id1, reports))
    print("  solo reports not id 1:   %d" % solo_not1)
    print("  Contact Count values:    %s" % dict(sorted(
        counts.items(), key=lambda kv: (kv[0] is None, kv[0]))))
    print("  count == tipped count:   %d/%d" % (count_matches, reports))
    print("  count==0 with contacts:  %d" % count_zero_with_tips)
else:
    print("  no reports captured (nothing touched the panel)")

print()
print("== driver output (what X received) ==")
down_at = None
presses = []
motion = 0
positions = []
try:
    with open(ev_path) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 4:
                continue
            t, typ, code, value = parts[0], parts[1], parts[2], parts[3]
            try:
                t = float(t)
                value = int(value)
            except ValueError:
                continue
            if typ == "EV_KEY" and code == "BTN_TOUCH":
                if value == 1 and down_at is None:
                    down_at = t
                    positions = []
                elif value == 0 and down_at is not None:
                    presses.append((down_at, t, len(positions), positions))
                    down_at = None
            elif code in ("ABS_MT_POSITION_X", "ABS_MT_POSITION_Y",
                          "ABS_X", "ABS_Y"):
                motion += 1
                if down_at is not None:
                    positions.append(value)
except FileNotFoundError:
    print("  (no evdev capture)")

if presses:
    durations = [(end - start) * 1000 for start, end, _n, _p in presses]
    print("  completed presses:       %d" % len(presses))
    print("  press durations (ms):    min %.0f  median %.0f  max %.0f"
          % (min(durations), sorted(durations)[len(durations) // 2],
             max(durations)))
    over = [d for d in durations if d > 190]
    print("  presses over 190ms:      %d" % len(over))
    for i, (start, end, n, pos) in enumerate(presses, 1):
        if pos:
            span = max(pos) - min(pos)
        else:
            span = 0
        print("    press %-3d %4.0fms  %2d motion values  raw span %d"
              % (i, (end - start) * 1000, n, span))
else:
    print("  completed presses:       0")
if down_at is not None:
    print("  WARNING: a press was still down when the capture ended")
print("  total motion values:     %d" % motion)
