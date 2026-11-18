#!/usr/bin/env python3
"""Find presses that were split into two by an empty/missing-id1 report."""
import json
import sys

HID = sys.argv[1] if len(sys.argv) > 1 else "/tmp/session/goodix-probe.jsonl"
EVD = sys.argv[2] if len(sys.argv) > 2 else "/tmp/session/goodix-evdev.log"
GRACE = 0.030

recs = []
for line in open(HID):
    line = line.strip()
    if line:
        recs.append(json.loads(line))


def ids_of(r):
    return [c[0] for c in r.get("contacts") or []]


def id1_of(r):
    for cid, x, y in r.get("contacts") or []:
        if cid == 1:
            return x, y
    return None


runs = []
cur = None
empties = []
for r in recs:
    t = r["t"]
    ids = ids_of(r)
    pos = id1_of(r)
    if cur is not None and t - cur["last"] > GRACE:
        cur["end"] = cur["last"]
        runs.append(cur)
        cur = None
    if pos is not None:
        if cur is None:
            cur = {"start": t, "last": t, "first": pos, "lastpos": pos}
        cur["last"] = t
        cur["lastpos"] = pos
        continue
    if not ids:
        empties.append(t)
        if cur is not None:
            cur["end"] = cur["last"]
            runs.append(cur)
            cur = None
    elif cur is not None:
        pass
if cur is not None:
    cur["end"] = cur["last"]
    runs.append(cur)

print("runs: %d" % len(runs))
print()
print("close pairs (gap < 250ms):")
found = 0
for i in range(1, len(runs)):
    a, b = runs[i - 1], runs[i]
    gap = (b["start"] - a["end"]) * 1000
    if gap < 250:
        found += 1
        between = [e for e in empties if a["end"] < e < b["start"]]
        print("  #%d->#%d gap %6.0fms  A %s->%s dur %.0f  B %s->%s dur %.0f"
              % (i, i + 1, gap, a["first"], a["lastpos"],
                 (a["end"] - a["start"]) * 1000,
                 b["first"], b["lastpos"],
                 (b["end"] - b["start"]) * 1000))
        print("       empty reports between: %d" % len(between))
if not found:
    print("  none")

print()
print("driver presses with gap < 250ms and their positions:")
presses = []
down = None
curpos = {}
for line in open(EVD):
    p = line.split()
    if len(p) < 4:
        continue
    try:
        t = float(p[0])
    except ValueError:
        continue
    typ, code, val = p[1], p[2], p[3]
    if typ == "EV_ABS" and code in ("ABS_MT_POSITION_X", "ABS_MT_POSITION_Y"):
        curpos[code] = int(val)
    if typ == "EV_KEY" and code == "BTN_TOUCH":
        v = int(val)
        if v == 1:
            down = t
            downpos = (curpos.get("ABS_MT_POSITION_X"),
                       curpos.get("ABS_MT_POSITION_Y"))
        elif v == 0 and down is not None:
            presses.append((down, t, downpos))
            down = None
found = 0
for i in range(1, len(presses)):
    gap = (presses[i][0] - presses[i - 1][1]) * 1000
    if gap < 250:
        found += 1
        a, b = presses[i - 1], presses[i]
        print("  gap %6.0fms  A dur %4.0fms at %s  B dur %4.0fms at %s"
              % (gap, (a[1] - a[0]) * 1000, a[2],
                 (b[1] - b[0]) * 1000, b[2]))
if not found:
    print("  none")
