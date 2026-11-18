#!/usr/bin/env python3
"""Analyse the firmware's contact identifiers across captured sessions.

The driver decodes report 0x0D itself, so unlike anything downstream of
hid-multitouch it still has the firmware's own 4-bit Contact Identifier for
every tipped block.  This script uses that field to characterise the faulty
contacts structurally rather than by geometry, and to test the rules that a
geometry filter cannot express.

Reads the jsonl written by probes/probe-hidraw.py:

    {"t": 115.797, "len": 54, "scan": 257, "count": 1,
     "contacts": [[1, 2575, 2192]]}

Input rows are `contacts` as (contact_id, x, y) in block order, so list order
is the order the firmware wrote the blocks in.

Usage: analyze-contact-ids.py capture.jsonl [capture2.jsonl ...]
"""

import collections
import json
import sys

# A contact is treated as sharing the other's column when X agrees this closely.
# Raw units, 0..0x3FFF across a 344 mm panel: 120 units is about 2.5 mm, and the
# tap-to-tap column tolerance already in the driver is the same 120.
SAME_COLUMN = 120
# ...and as being in a different row when Y differs by at least this much.
# Raw units, 0..0x257F across 196 mm: 400 units is about 3 cm.
DIFF_ROW = 400


def load(path):
    frames = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            frames.append((r.get("t"), [tuple(c) for c in r.get("contacts", [])]))
    return frames


def gestures(frames):
    """Split into runs of consecutive frames that have at least one contact."""
    run = []
    for t, contacts in frames:
        if contacts:
            run.append((t, contacts))
        elif run:
            yield run
            run = []
    if run:
        yield run


def pair_geometry(frame):
    """Pairs of contacts in one frame that share a column but not a row."""
    out = []
    for i in range(len(frame)):
        for j in range(i + 1, len(frame)):
            (ida, xa, ya), (idb, xb, yb) = frame[i], frame[j]
            if abs(xa - xb) <= SAME_COLUMN and abs(ya - yb) >= DIFF_ROW:
                out.append((ida, idb))
    return out


def analyse(path):
    frames = load(path)
    tipped = [c for _, c in frames if c]
    dup = [c for c in tipped if len({x[0] for x in c}) != len(c)]
    multi = [c for c in tipped if len(c) > 1]

    print("=" * 72)
    print("%s" % path)
    print("  frames %d, tipped %d, multi-contact %d (%.1f%% of tipped)"
          % (len(frames), len(tipped), len(multi),
             100.0 * len(multi) / max(1, len(tipped))))
    print("  contact-count histogram: %s"
          % dict(collections.Counter(len(c) for c in tipped)))

    print("  frames with a DUPLICATE contact id: %d (%.1f%% of multi)"
          % (len(dup), 100.0 * len(dup) / max(1, len(multi))))
    for c in dup[:3]:
        print("     e.g. %s" % (c,))

    pairs = [p for c in multi for p in pair_geometry(c)]
    print("  frames with a same-column/different-row PAIR: %d (%.1f%% of multi)"
          % (sum(1 for c in multi if pair_geometry(c)),
             100.0 * sum(1 for c in multi if pair_geometry(c)) / max(1, len(multi))))

    # Per-gesture id lifetimes.  In a gesture the finger that is there from
    # beginning to end is the real one; anything transient is fabricated.
    life = collections.Counter()
    solo_only = collections.Counter()
    n_gest = 0
    for g in gestures(frames):
        n_gest += 1
        counts = collections.Counter()
        for _, c in g:
            for cid, _, _ in c:
                counts[cid] += 1
        for cid, n in counts.items():
            life[n] += 1
            if n == 1:
                solo_only[cid] += 1

    print("  gestures: %d" % n_gest)
    hist = collections.Counter()
    for g in gestures(frames):
        hist[len({cid for _, c in g for cid, _, _ in c})] += 1
    print("  distinct ids per gesture: %s" % dict(sorted(hist.items())))

    # For each frame, where does the real finger sit?  "Real" = the id that is
    # present for the most frames of its gesture.
    real_rank = collections.Counter()
    real_id_val = collections.Counter()
    phantom_id_val = collections.Counter()
    phantom_x_shared = 0
    phantom_total = 0
    for g in gestures(frames):
        counts = collections.Counter()
        for _, c in g:
            for cid, _, _ in c:
                counts[cid] += 1
        if not counts:
            continue
        real = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        for _, c in g:
            ids = [x[0] for x in c]
            if real in ids:
                real_rank[ids.index(real)] += 1
                real_id_val[real] += 1
            for cid, _, _ in c:
                if cid == real:
                    continue
                phantom_total += 1
                phantom_id_val[cid] += 1
                rx = [x for i, (i2, x, _) in enumerate(c) if i2 == real]
                cx = [x for i2, x, _ in c if i2 == cid]
                if rx and cx and abs(rx[0] - cx[0]) <= SAME_COLUMN:
                    phantom_x_shared += 1

    print("  block index of the real finger, per frame: %s"
          % dict(sorted(real_rank.items())))
    print("  contact id of the real finger: %s" % dict(sorted(real_id_val.items())))
    if phantom_total:
        top = phantom_id_val.most_common(8)
        print("  fabricated blocks: %d, sharing the real column: %d (%.1f%%)"
              % (phantom_total, phantom_x_shared,
                 100.0 * phantom_x_shared / phantom_total))
        print("  contact id of fabricated blocks (top): %s" % (top,))
    return {
        "frames": len(frames),
        "tipped": len(tipped),
        "multi": len(multi),
        "dup": len(dup),
        "phantom": phantom_total,
        "phantom_shared": phantom_x_shared,
    }


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    for path in argv[1:]:
        analyse(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
