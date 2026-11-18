#!/usr/bin/env python3
"""Replay a captured session through the driver and report what reaches the page.

This drives the real Panel/Emitter code from a goodix-probe jsonl capture with a
fake uinput device and the capture's own timestamps, so it measures the policy
the kiosk actually runs rather than a stand-in for it.

The interesting number is the jump between consecutive emitted Y values.  A
finger cannot move 400 raw units between two 100 Hz reports, so any emitted Y
delta above that is the page being told the finger teleported.

Usage:
    simulate-continuity-tracker.py <driver.py> <capture.jsonl>
"""

import importlib.util
import json
import sys


def load_driver(path):
    spec = importlib.util.spec_from_file_location("gd", path)
    gd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gd)
    gd.DEBUG = False
    gd.log = lambda msg: None
    return gd


class FakeUInput:
    def __init__(self, *a, **k):
        self.events = []

        class Dev:
            path = None
            fd = -1
        self.device = Dev()

    def write_event(self, ev):
        self.events.append((ev.code, ev.value))

    def close(self):
        pass


def pct(values, p):
    if not values:
        return 0
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))]


def run(gd, frames):
    gd.UInput = FakeUInput
    out = gd.Emitter.__new__(gd.Emitter)
    out.ui = FakeUInput()
    out.tid = 0
    out.down = False
    out.sent = (None, None)
    panel = gd.Panel(out)

    for r in frames:
        t = r["t"]
        panel.feed([tuple(c) for c in r["contacts"]], t)
        panel.tick(t)

    # Walk the event stream, resetting at each press: a delta measured across a
    # release is the distance between two gestures, not finger motion.  The
    # tracking id is emitted first in a press block, so it is the marker to key
    # on; BTN_TOUCH comes after the coordinates.  Only the MT axis pair is
    # counted, because the driver emits ABS_X/ABS_Y with the same values.
    dx, dy = [], []
    last_x = last_y = None
    for code, value in out.ui.events:
        if code == gd.e.ABS_MT_TRACKING_ID:
            if value >= 0:
                last_x = last_y = None
        elif code == gd.e.ABS_MT_POSITION_X:
            if last_x is not None:
                dx.append(abs(value - last_x))
            last_x = value
        elif code == gd.e.ABS_MT_POSITION_Y:
            if last_y is not None:
                dy.append(abs(value - last_y))
            last_y = value

    return {
        "presses": panel.presses,
        "ghost": panel.ghost_presses,
        "stuck": panel.stuck_presses,
        "phantom_frames": panel.phantom_frames,
        "y_events": len(dy),
        "x_events": len(dx),
        "dy_p50": pct(dy, 0.5),
        "dy_p99": pct(dy, 0.99),
        "dy_max": max(dy) if dy else 0,
        "dy_over_step": sum(1 for d in dy if d > 400),
        "dx_p99": pct(dx, 0.99),
        "dx_max": max(dx) if dx else 0,
        "dx_total": sum(dx),
        "dy_total": sum(dy),
    }


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    gd = load_driver(argv[1])
    frames = [json.loads(l) for l in open(argv[2]) if l.strip()]
    r = run(gd, frames)
    print("%s" % argv[2])
    print("  presses %-4d ghost-dropped %-3d stuck-released %-3d "
          "phantom-frames %d"
          % (r["presses"], r["ghost"], r["stuck"], r["phantom_frames"]))
    print("  emitted Y deltas: n=%d p50=%d p99=%d max=%d  (>%d: %d)"
          % (r["y_events"], r["dy_p50"], r["dy_p99"], r["dy_max"],
             400, r["dy_over_step"]))
    print("  emitted X deltas: n=%d p99=%d max=%d  total|dX|=%d  total|dY|=%d"
          % (r["x_events"], r["dx_p99"], r["dx_max"], r["dx_total"],
             r["dy_total"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
