#!/usr/bin/env python3
"""Compare raw HID captures by the measures that matter for the latch.

Usage:
    analyze-modes.py LABEL:PATH [LABEL:PATH ...]

Each PATH is a goodix-probe.jsonl written by probe-hidraw.py.  Prints, per
capture: report rate, contacts per report, the column-lock statistic (spread of
X versus spread of Y inside a multi-contact report), count laws, Y edge pinning,
and every contact that lived longer than 0.8 s (a candidate latch).

The comparison it exists for: mode-2 (multitouch) against mode-1 (single
touch).  See docs/FIRMWARE-LATCH.md section 8.
"""
import json
import statistics
import sys


def load(path):
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def lifespans(rows):
    """Per contact id, the runs where it is continuously present."""
    open_ids = {}
    spans = []
    for i, row in enumerate(rows):
        current = {c[0]: c for c in row["contacts"]}
        for cid in list(open_ids):
            if cid not in current:
                start, last = open_ids.pop(cid)
                spans.append((cid, start, last))
        for cid in current:
            if cid in open_ids:
                open_ids[cid][1] = i
            else:
                open_ids[cid] = [i, i]
    for cid, (start, last) in open_ids.items():
        spans.append((cid, start, last))
    return spans


def describe(label, rows):
    print("== %s  (%d reports)" % (label, len(rows)))
    if not rows:
        print("   no reports\n")
        return

    span = rows[-1]["t"] - rows[0]["t"]
    gaps = [rows[i]["t"] - rows[i - 1]["t"] for i in range(1, len(rows))]
    rate = (len(rows) - 1) / span if span > 0 else 0
    print("   duration %.1f s, %.1f reports/s, median gap %.1f ms"
          % (span, rate, 1000 * statistics.median(gaps) if gaps else 0))

    hist = {}
    for row in rows:
        hist[len(row["contacts"])] = hist.get(len(row["contacts"]), 0) + 1
    print("   contacts per report: " + ", ".join(
        "%d->%d" % (k, hist[k]) for k in sorted(hist)))

    multi = [row["contacts"] for row in rows if len(row["contacts"]) >= 2]
    if multi:
        sx = [max(c[1] for c in m) - min(c[1] for c in m) for m in multi]
        sy = [max(c[2] for c in m) - min(c[2] for c in m) for m in multi]
        print("   multi-contact: %d reports (%.0f%%); X spread median %d, "
              "Y spread median %d; X spread <50 in %d"
              % (len(multi), 100 * len(multi) / len(rows),
                 statistics.median(sx), statistics.median(sy),
                 sum(1 for v in sx if v < 50)))
    else:
        print("   multi-contact: none")

    less = same = plus1 = more = 0
    for row in rows:
        tipped, count = len(row["contacts"]), row["count"]
        if count < tipped:
            less += 1
        elif count == tipped:
            same += 1
        elif count == tipped + 1:
            plus1 += 1
        else:
            more += 1
    print("   count laws: <tipped %d, ==tipped %d, ==+1 %d, >+1 %d"
          % (less, same, plus1, more))

    contacts = [c for row in rows for c in row["contacts"]]
    if contacts:
        low = sum(1 for c in contacts if c[2] in (0, 1))
        high = sum(1 for c in contacts if c[2] >= 9590)
        print("   Y pinned low %d (%.1f%%), high %d (%.1f%%)"
              % (low, 100 * low / len(contacts),
                 high, 100 * high / len(contacts)))

    long_spans = []
    for cid, start, last in lifespans(rows):
        dur = rows[last]["t"] - rows[start]["t"]
        if dur > 0.8:
            xs = [c[1] for row in rows[start:last + 1] for c in row["contacts"]
                  if c[0] == cid]
            ys = [c[2] for row in rows[start:last + 1] for c in row["contacts"]
                  if c[0] == cid]
            long_spans.append((cid, dur, min(xs), max(xs), min(ys), max(ys)))
    if long_spans:
        print("   spans > 0.8 s:")
        for cid, dur, x0, x1, y0, y1 in long_spans:
            print("     id %d  %.2f s  X %d..%d (%d)  Y %d..%d (%d)"
                  % (cid, dur, x0, x1, x1 - x0, y0, y1, y1 - y0))
    else:
        print("   spans > 0.8 s: none")
    print()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for arg in sys.argv[1:]:
        if ":" not in arg:
            print("expected LABEL:PATH, got %r" % arg)
            return 2
        label, path = arg.split(":", 1)
        describe(label, load(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
