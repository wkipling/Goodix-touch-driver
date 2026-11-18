#!/usr/bin/env python3
"""Replay captures through the driver's press logic and test a ghost-press rule.

Usage:
    simulate-ghost-press-filter.py CAPTURE [CAPTURE ...]
    simulate-ghost-press-filter.py --margin CAPTURE [CAPTURE ...]
    simulate-ghost-press-filter.py --blips CAPTURE [CAPTURE ...]
    simulate-ghost-press-filter.py --bases CAPTURE [CAPTURE ...]

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
legitimate press it must keep.

--blips prints the presses that lasted 25 ms or less and never moved.  Both
captured ghosts are in that set, but so are several presses the rule does not
touch, which is where its coverage ends; see docs/FINDINGS-ADDENDUM.md.

--bases prints the pairs inside the gap window that would be judged
differently if the previous press's *end* were the reference instead of its
origin.  That is the open question about the rule's shape, not a tuning knob.

The thresholds below are the driver's defaults (TOUCH_GHOST_GAP_MS / _DX /
_DY); keep them in step with src/.
"""

GRACE = 0.030        # seconds of id-1 absence before the driver releases
GAP_MS = 120.0       # rule: end of the previous press to this press start
DX = 120             # rule: same column, in raw units
DY = 400             # rule: different row, in raw units

BLIP_MS = 25         # a press this short is 2-3 frames; a human tap is longer
BLIP_DISP = 5        # ... and it never moved


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
    """Return (kept, dropped) under the ghost-press rule.

    Matches the driver: the new press is compared against where the previous
    press *started*.  --bases shows what changes if the comparison point is
    where the previous press ended instead.
    """
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


def displaced(p):
    """How far the finger moved between the first and last frame of a press."""
    return ((p.get("x_end", p["x"]) - p["x"]) ** 2 +
            (p.get("y_end", p["y"]) - p["y"]) ** 2) ** 0.5


def blips(paths):
    """Show the ultra-short stationary presses, and which the rule covers.

    Both captured ghosts are 20 ms or less and never move, which is 2-3 report
    frames -- a human tap is longer and moves a little.  But not every such
    blip is a ghost the rule can see: it only looks GAP_MS back, so a blip that
    arrives later reaches the page as a press.  This mode makes that boundary
    explicit instead of leaving it implied by the rule's parameters.
    """
    covered = uncovered = 0
    shortest_real = None
    print("ultra-short stationary presses (<= %d ms, moved <= %d raw units):"
          % (BLIP_MS, BLIP_DISP))
    print("%-22s %5s %5s %6s %5s %5s  %-16s %s"
          % ("capture", "dur", "disp", "gap", "dX", "dY", "at", "rule"))
    for path in paths:
        ps = presses(load(path))
        kept, dropped = apply_rule(ps)
        dropped_ids = set(id(p) for p, _ in dropped)
        for i, p in enumerate(ps):
            d = displaced(p)
            if p["dur_ms"] > BLIP_MS or d > BLIP_DISP:
                if shortest_real is None or p["dur_ms"] < shortest_real:
                    shortest_real = p["dur_ms"]
                continue
            prev = ps[i - 1] if i else None
            if prev is None:
                gap = dx = dy = -1
            else:
                gap = 1000 * (p["start"] - prev["end"])
                dx = abs(p["x"] - prev["x"])
                dy = abs(p["y"] - prev["y"])
            hit = id(p) in dropped_ids
            covered += 1 if hit else 0
            uncovered += 0 if hit else 1
            print("%-22s %5.0f %5.0f %6.0f %5d %5d  %-16s %s"
                  % (path.split("/")[-1][:22], p["dur_ms"], d, gap, dx, dy,
                     "(%d,%d)" % (p["x"], p["y"]),
                     "DROPPED" if hit else "reported"))
    print()
    print("%d blips: %d dropped by the rule, %d reach the page"
          % (covered + uncovered, covered, uncovered))
    if shortest_real is not None:
        print("shortest press that was not a blip: %.0f ms" % shortest_real)
        print("a duration-only rule would have %.0f ms of margin - too thin"
              % (shortest_real - BLIP_MS))


def _matches(cur, px, py):
    return abs(cur["x"] - px) <= DX and abs(cur["y"] - py) >= DY


def bases(paths):
    """Show where the two possible comparison points disagree.

    The driver compares the new press against where the previous press
    *started*.  Comparing against where it *ended* catches more, and also
    suppresses ordinary taps that follow a swipe too closely.  This mode lists
    every pair inside the gap window the two bases judge differently, so the
    choice can be made on evidence rather than taste.
    """
    print("press pairs inside the %.0f ms window that the two bases judge "
          "differently\n" % GAP_MS)
    print("%-22s %6s %-24s %s" % ("capture", "gap", "previous press",
                                   "verdict"))
    disagree = 0
    for path in paths:
        ps = presses(load(path))
        for prev, cur in zip(ps, ps[1:]):
            gap = 1000 * (cur["start"] - prev["end"])
            if not (0 <= gap <= GAP_MS):
                continue
            by_origin = _matches(cur, prev["x"], prev["y"])
            by_end = _matches(cur, prev["x_end"], prev["y_end"])
            if by_origin == by_end:
                continue
            disagree += 1
            print("%-22s %6.0f dur %-4.0f moved %-6.0f %s"
                  % (path.split("/")[-1][:22], gap, prev["dur_ms"],
                     displaced(prev),
                     "end drops, origin keeps" if by_end
                     else "origin drops, end keeps"))
            print("        new press (%d,%d) dur %.0f"
                  % (cur["x"], cur["y"], cur["dur_ms"]))
    print("\n%d disagreements.  Every one here follows a press that moved, so "
          "the data\n cannot say whether it is a ghost or a tap after a swipe."
          % disagree)


def main():
    import sys
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    for flag, fn in (("--margin", margin), ("--blips", blips),
                     ("--bases", bases)):
        if args[0] == flag:
            if len(args) < 2:
                print(__doc__)
                return 2
            fn(args[1:])
            return 0
    for path in args:
        describe(path)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
