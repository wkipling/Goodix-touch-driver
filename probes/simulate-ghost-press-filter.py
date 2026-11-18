#!/usr/bin/env python3
"""Replay captures through the driver's press logic and test a ghost-press rule.

Usage:
    simulate-ghost-press-filter.py CAPTURE [CAPTURE ...]
    simulate-ghost-press-filter.py --margin CAPTURE [CAPTURE ...]

The driver presses when contact id 1 appears and releases when it has been
absent for 30 ms.  Replaying that yields a list of presses.  The default mode
then asks whether a simple rule would remove the impossible ones:

    a press that begins within GAP_MS of the end of the previous press, at the
    same X (within DX) but a different row (|dY| >= DY), is a phantom and is
    dropped.

The point of the run is not to show the rule drops presses - it is to show
*which* presses it drops, and whether any of them look like a human tap.

--margin prints every consecutive press pair whose X agrees within 200 raw
units, sorted by gap, with the verdict.  That is the safety argument: it shows
how much empty space stands between the presses the rule drops and the nearest
legitimate press it must keep.  The thresholds below are the driver's defaults
(TOUCH_GHOST_GAP_MS / _DX / _DY); keep them in step with src/.
"""

GRACE = 0.030        # seconds of id-1 absence before the driver releases
GAP_MS = 120.0       # rule: end of the previous press to this press start
DX = 120             # rule: same column, in raw units
DY = 400             # rule: different row, in raw units


def load(path):
    import json
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def presses(rows):
    """Replay the driver's id-1 state machine into a list of presses.

    The panel is change-driven and silent between taps, so the release cannot
    depend on an incoming report: the driver expires a press when id 1 has been
    absent for GRACE seconds of wall time.  The replay has to do the same, or
    every tap merges into the previous one.
    """
    out = []
    current = None
    last_seen = None
    for row in rows:
        if current is not None and last_seen is not None and \
                row["t"] - last_seen >= GRACE:
            out.append(current)
            current = None
            last_seen = None

        real = [(c[1], c[2]) for c in row["contacts"] if c[0] == 1]
        if not real:
            continue
        if current is None:
            current = {"start": row["t"], "x": real[0][0], "y": real[0][1]}
        current["end"] = row["t"]
        current["x_end"], current["y_end"] = real[0]
        last_seen = row["t"]

    if current is not None:
        out.append(current)
    for p in out:
        p["dur_ms"] = 1000 * (p["end"] - p["start"])
    return out


def apply_rule(press_list):
    """Return (kept, dropped) under the ghost-press rule."""
    kept, dropped = [], []
    prev = None
    for press in press_list:
        ghost = False
        if prev is not None:
            gap = 1000 * (press["start"] - prev["end"])
            dx = abs(press["x"] - prev["x"])
            dy = abs(press["y"] - prev["y"])
            if 0 <= gap <= GAP_MS and dx <= DX and dy >= DY:
                ghost = True
        if ghost:
            dropped.append((press, prev))
        else:
            kept.append(press)
            prev = press
    return kept, dropped


def describe(path):
    rows = load(path)
    press_list = presses(rows)
    kept, dropped = apply_rule(press_list)
    print("== %s" % path)
    print("   %d presses replayed; rule drops %d, keeps %d"
          % (len(press_list), len(dropped), len(kept)))
    for press, prev in dropped:
        print("      dropped t=%.2f  at (%d,%d) dur %.0f ms"
              % (press["start"], press["x"], press["y"], press["dur_ms"]))
        print("        previous  t=%.2f  at (%d,%d) dur %.0f ms  gap %.0f ms"
              % (prev["start"], prev["x"], prev["y"], prev["dur_ms"],
                 1000 * (press["start"] - prev["end"])))
    short = [p for p in kept if p["dur_ms"] < 250]
    print("   kept presses under 250 ms: %d (these are the taps that matter)"
          % len(short))
    print()


def margin(paths):
    """Print the pair table that justifies the thresholds."""
    print("rule: gap <= %dms, dX <= %d, dY >= %d raw units"
          % (GAP_MS, DX, DY))
    print("dx/dy are from the previous press's origin to this press's origin\n")
    for path in paths:
        pairs = []
        press_list = presses(load(path))
        for prev, cur in zip(press_list, press_list[1:]):
            gap = 1000 * (cur["start"] - prev["end"])
            dx = abs(cur["x"] - prev["x"])
            dy = abs(cur["y"] - prev["y"])
            if dx <= 200:                      # same column, loosely
                pairs.append((gap, dx, dy, cur, prev))
        pairs.sort()
        print("== %s  (%d presses, %d column-sharing pairs)"
              % (path.split("/")[-1], len(press_list), len(pairs)))
        if not pairs:
            print("   none\n")
            continue
        print("   %7s %5s %6s %6s  %-18s %s"
              % ("gap_ms", "dX", "dY", "dur_ms", "at", "verdict"))
        for gap, dx, dy, cur, prev in pairs:
            verdict = ("DROPPED" if 0 <= gap <= GAP_MS and
                       dx <= DX and dy >= DY else "kept")
            print("   %7.0f %5d %6d %6.0f  %-18s %s"
                  % (gap, dx, dy, cur["dur_ms"],
                     "(%d,%d)" % (cur["x"], cur["y"]), verdict))
        dropped_gaps = [g for g, _dx, dy, _c, _p in pairs
                        if 0 <= g <= GAP_MS and dy >= DY]
        kept_gaps = [g for g, _dx, dy, _c, _p in pairs
                     if not (0 <= g <= GAP_MS and dy >= DY)]
        if dropped_gaps and kept_gaps:
            print("   margin: ghosts at %s ms, nearest kept pair at %.0f ms"
                  % (", ".join("%.0f" % g for g in dropped_gaps),
                     min(kept_gaps)))
        print()


def main():
    import sys
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    if args[0] == "--margin":
        if len(args) < 2:
            print(__doc__)
            return 2
        margin(args[1:])
        return 0
    for path in args:
        describe(path)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
