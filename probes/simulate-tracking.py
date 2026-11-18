#!/usr/bin/env python3
"""Compare the driver's "trust contact id 1" rule against continuity tracking.

Usage:
    simulate-tracking.py CAPTURE [CAPTURE ...]

Both rules are replayed over each gesture in a capture.  The driver currently
takes the contact with id 1 and nothing else.  The alternative keeps the last
accepted position and, each frame, takes whichever contact is nearest to it,
falling back to id 1 when the gesture starts.

The question is not which rule looks smoother — a nearest-neighbour rule is
smooth by construction, so that would prove nothing.  The question is how often
they disagree, and whether the disagreements are the jumps that put a tap in the
wrong row.  Reported per capture:

  gestures                  maximal runs of frames with contacts, split on a gap
  id-1 jumps / continuity   frame-to-frame displacements over 200 and 500 units
  disagreements             frames where the two rules pick different contacts
  A-jump, B-smooth          the interesting case: id 1 leaps while a contact
                            continues from the tracked position
  B-jump, A-smooth          the reverse, which would argue against continuity
"""
import json
import sys

JUMP_SMALL = 200      # raw units, ~20 CSS px: a wrong-row event
JUMP_BIG = 500        # unmistakable
GAP_SPLIT = 0.030     # seconds without contacts ends a gesture


def load(path):
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def gestures(rows):
    """Split into runs of frames that have at least one contact."""
    out = []
    current = []
    last_t = None
    for row in rows:
        if not row["contacts"]:
            if current:
                out.append(current)
                current = []
            last_t = None
            continue
        if last_t is not None and row["t"] - last_t > GAP_SPLIT and current:
            out.append(current)
            current = []
        current.append(row)
        last_t = row["t"]
    if current:
        out.append(current)
    return out


def dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def run(rules, frames):
    """Return {rule: [(x, y), ...]} for one gesture, plus where they differ."""
    tracks = {name: [] for name in rules}
    chosen = {name: [] for name in rules}
    disagreements = []
    for name in rules:
        prev = None
        for row in frames:
            contacts = [(c[1], c[2]) for c in row["contacts"]]
            id1 = [(c[1], c[2]) for c in row["contacts"] if c[0] == 1]
            if name == "id1":
                pick = id1[0] if id1 else None
            else:
                if prev is None:
                    pick = id1[0] if id1 else contacts[0]
                else:
                    pick = min(contacts, key=lambda c: dist(c, prev))
            if pick is not None:
                tracks[name].append(pick)
                prev = pick
            chosen[name].append(pick)
    for i in range(len(frames)):
        a = chosen["id1"][i]
        b = chosen["continuity"][i]
        if a and b and dist(a, b) > JUMP_SMALL:
            disagreements.append((i, a, b))
    return tracks, disagreements


def describe(path):
    rows = load(path)
    gs = gestures(rows)
    stats = {"gestures": len(gs), "a_small": 0, "a_big": 0, "b_small": 0,
             "b_big": 0, "disagree": 0, "a_jump_b_smooth": 0,
             "b_jump_a_smooth": 0, "a_max": 0, "b_max": 0}
    examples = []
    for frames in gs:
        tracks, disagreements = run(("id1", "continuity"), frames)
        stats["disagree"] += len(disagreements)
        for name, small, big, key in (("id1", "a_small", "a_big", "a_max"),
                                      ("continuity", "b_small", "b_big", "b_max")):
            track = tracks[name]
            worst = 0
            for i in range(1, len(track)):
                step = dist(track[i], track[i - 1])
                worst = max(worst, step)
                if step > JUMP_SMALL:
                    stats[small] += 1
                if step > JUMP_BIG:
                    stats[big] += 1
            stats[key] = max(stats[key], worst)
        if disagreements:
            i, a, b = disagreements[0]
            examples.append((frames[0]["t"], a, b))
    print("== %s" % path)
    print("   gestures %d, disagreements %d frames"
          % (stats["gestures"], stats["disagree"]))
    print("   id1        steps >%d: %d, >%d: %d, worst %d units"
          % (JUMP_SMALL, stats["a_small"], JUMP_BIG, stats["a_big"], stats["a_max"]))
    print("   continuity steps >%d: %d, >%d: %d, worst %d units"
          % (JUMP_SMALL, stats["b_small"], JUMP_BIG, stats["b_big"], stats["b_max"]))
    for t, a, b in examples[:4]:
        print("   first disagreement at t=%.2f: id1 %s vs continuity %s"
              % (t, a, b))
    print()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for path in sys.argv[1:]:
        describe(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
