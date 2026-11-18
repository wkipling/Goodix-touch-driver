#!/usr/bin/env python3
"""Reconstruct each gesture from the raw HID capture and the driver output.

    python3 analyze-session.py /tmp/session/goodix-probe.jsonl /tmp/session/goodix-evdev.log

Prints one row per HID contact-id-1 run (a physical press) and flags:
  * an empty report inside the run (the driver releases immediately there),
  * how many motion events the 80-unit deadband would emit,
  * the largest single emitted move, in CSS px (Chromium's slop is 8),
  * press duration.
Also prints the driver's press/release pairs and their gaps.
"""
import json
import sys

HID = sys.argv[1] if len(sys.argv) > 1 else "/tmp/session/goodix-probe.jsonl"
EVD = sys.argv[2] if len(sys.argv) > 2 else "/tmp/session/goodix-evdev.log"

FUZZ = 80
GRACE = 0.030
RAW_X_CSS = 16384 / 1536.0      # 10.6667
RAW_Y_CSS = 9600 / 960.0        # 10.0

recs = []
with open(HID) as f:
    for line in f:
        line = line.strip()
        if line:
            recs.append(json.loads(line))


def id1_of(rec):
    for cid, x, y in rec.get("contacts") or []:
        if cid == 1:
            return x, y
    return None


def ids_of(rec):
    return [c[0] for c in rec.get("contacts") or []]


runs = []
cur = None
empties = []
for r in recs:
    t = r["t"]
    ids = ids_of(r)
    pos = id1_of(r)

    # The driver's tick releases a press 30ms after the last id 1, including
    # when the panel has gone silent (no report at all).
    if cur is not None and t - cur["last"] > GRACE:
        cur["end"] = cur["last"]
        runs.append(cur)
        cur = None

    if pos is not None:
        if cur is None:
            cur = {"start": t, "last": t, "recs": [], "first": pos,
                   "phantom_ids": set(), "counts": []}
        cur["last"] = t
        cur["recs"].append((t, pos, r))
        cur["phantom_ids"].update(c for c in ids if c != 1)
        cur["counts"].append(r.get("count"))
        continue

    # No id 1. An empty report releases the driver immediately; that is how a
    # normal lift ends, but it also splits a press if it happens mid-touch.
    if not ids:
        empties.append(t)
        if cur is not None:
            cur["end"] = cur["last"]
            runs.append(cur)
            cur = None
    elif cur is not None:
        cur["phantom_ids"].update(c for c in ids if c != 1)
        cur["counts"].append(r.get("count"))

if cur is not None:
    cur["end"] = cur["last"]
    runs.append(cur)

for g in runs:
    g["empty_inside"] = sum(1 for t in empties if g["start"] < t < g["last"])

print("== physical gestures (HID id 1 runs, %d) ==" % len(runs))
print("  #   dur   n   empty  moves  maxmove(css x/y)  net(css x/y)  "
      "net_disp  phantoms  count")
suspicious = []
for n, g in enumerate(runs, 1):
    dur = (g["end"] - g["start"]) * 1000
    # simulate the deadband against the last emitted value
    ex, ey = g["first"]
    moves = 0
    maxdx = maxdy = 0
    for t, (x, y), r in g["recs"]:
        dx, dy = abs(x - ex), abs(y - ey)
        if dx >= FUZZ or dy >= FUZZ:
            moves += 1
            if dx >= FUZZ:
                maxdx = max(maxdx, dx)
                ex = x
            if dy >= FUZZ:
                maxdy = max(maxdy, dy)
                ey = y
    x0, y0 = g["first"]
    xN, yN = g["recs"][-1][1]
    netdx = (xN - x0) / RAW_X_CSS
    netdy = (yN - y0) / RAW_Y_CSS
    net = (netdx ** 2 + netdy ** 2) ** 0.5
    counts = [c for c in g["counts"] if c is not None]
    counttxt = "%d..%d" % (min(counts), max(counts)) if counts else "-"
    flag = ""
    if g["empty_inside"]:
        flag += " EMPTY"
    if dur > 170:
        flag += " LONG"
    if maxdx / RAW_X_CSS > 8 or maxdy / RAW_Y_CSS > 8:
        flag += " JUMP"
    if flag:
        suspicious.append((n, flag.strip(), dur, moves, maxdx, maxdy, net))
    print("%3d %5.0f %3d %6d %6d   %5.0f/%-5.0f     %5.0f/%-5.0f  %6.0f   %-9s %s%s"
          % (n, dur, len(g["recs"]), g["empty_inside"], moves,
             maxdx / RAW_X_CSS, maxdy / RAW_Y_CSS, netdx, netdy, net,
             ",".join(str(c) for c in sorted(g["phantom_ids"])) or "-",
             counttxt, flag))

print()
print("== driver press/release pairs ==")
presses = []
down = None
for line in open(EVD):
    p = line.split()
    if len(p) < 4 or p[1] != "EV_KEY" or p[2] != "BTN_TOUCH":
        continue
    t, v = float(p[0]), int(p[3])
    if v == 1 and down is None:
        down = t
    elif v == 0 and down is not None:
        presses.append((down, t))
        down = None
print("  count: %d" % len(presses))
gaps = []
for i in range(1, len(presses)):
    gaps.append((presses[i][0] - presses[i - 1][1]) * 1000)
if gaps:
    print("  inter-press gap ms: min %.0f median %.0f max %.0f"
          % (min(gaps), sorted(gaps)[len(gaps) // 2], max(gaps)))
short = [(a, b) for a, b in presses if (b - a) * 1000 < 25]
print("  presses shorter than 25ms: %d" % len(short))
if down is not None:
    print("  WARNING: press still down at end of capture")

print()
print("== flagged gestures ==")
for n, flag, dur, moves, maxdx, maxdy, net in suspicious:
    print("  #%-3d %-5s dur %.0fms moves %d  max move %.0f/%.0f css  net %.0f css"
          % (n, flag, dur, moves, maxdx / RAW_X_CSS, maxdy / RAW_Y_CSS, net))
if not suspicious:
    print("  none")
