#!/usr/bin/env python3
"""Simulate a stuck-contact detector over the captured sessions.

A press is "stuck" when the firmware keeps id 1 asserted but moves it in a
large, repeating sweep on one axis while the other stays put.  A real drag is
either directed (low reversal count) or short.

Usage: stuck-detector-sim.py FILE.jsonl [...]
"""
import json
import sys

GRACE = 0.030
WINDOW = 0.60          # seconds of history considered
MIN_PRESS = 0.30       # do not judge a press younger than this
SPAN = 1500.0          # raw units of sweep on the moving axis
QUIET_AXIS = 350.0     # raw units allowed on the other axis
REV_DELTA = 250.0      # raw units that count as a directional step
REV_MIN = 3            # reversals inside WINDOW
NET_FRAC = 0.5         # net displacement must be < SPAN * NET_FRAC


def p1(r):
    for c in r["contacts"]:
        if c[0] == 1:
            return (c[1], c[2])
    return None


def ids(r):
    return [c[0] for c in r["contacts"]]


def runs(recs):
    out = []
    cur = None
    for r in recs:
        t = r["t"]
        p = p1(r)
        ii = ids(r)
        if cur and t - cur["last"] > GRACE:
            cur["end"] = cur["last"]
            out.append(cur)
            cur = None
        if p:
            if not cur:
                cur = {"start": t, "last": t, "samples": []}
            cur["last"] = t
            cur["samples"].append((t, p[0], p[1]))
            continue
        if not ii and cur:
            cur["end"] = cur["last"]
            out.append(cur)
            cur = None
    if cur:
        cur["end"] = cur["last"]
        out.append(cur)
    return out


def reversals(samples, axis):
    n = 0
    last_sign = 0
    last = None
    for _t, x, y in samples:
        v = x if axis == 0 else y
        if last is not None:
            d = v - last
            if abs(d) >= REV_DELTA:
                sign = 1 if d > 0 else -1
                if last_sign and sign != last_sign:
                    n += 1
                last_sign = sign
        last = v
    return n


def fired_at(g):
    start = g["start"]
    for i, (t, _x, _y) in enumerate(g["samples"]):
        if t - start < MIN_PRESS:
            continue
        lo = t - WINDOW
        window = [s for s in g["samples"][: i + 1] if s[0] >= lo]
        if len(window) < 4:
            continue
        xs = [s[1] for s in window]
        ys = [s[2] for s in window]
        sx = max(xs) - min(xs)
        sy = max(ys) - min(ys)
        netx = abs(xs[-1] - xs[0])
        nety = abs(ys[-1] - ys[0])
        if sx >= SPAN and sy <= QUIET_AXIS and \
                reversals(window, 0) >= REV_MIN and netx < sx * NET_FRAC:
            return t - start, sx, sy, reversals(window, 0)
        if sy >= SPAN and sx <= QUIET_AXIS and \
                reversals(window, 1) >= REV_MIN and nety < sy * NET_FRAC:
            return t - start, sx, sy, reversals(window, 1)
    return None


for path in sys.argv[1:]:
    recs = [json.loads(l) for l in open(path) if l.strip()]
    rs = runs(recs)
    print("== %s: %d presses ==" % (path, len(rs)))
    for i, g in enumerate(rs, 1):
        dur = (g["end"] - g["start"]) * 1000
        f = fired_at(g)
        if f or dur > 190:
            if f:
                at, sx, sy, rev = f
                print("  #%-3d dur %6.0fms  FIRE at %5.0fms  span %.0f/%.0f rev %d"
                      % (i, dur, at * 1000, sx, sy, rev))
            else:
                print("  #%-3d dur %6.0fms  no fire" % (i, dur))
