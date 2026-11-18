"""Drift scenarios, built from paths measured on the real panel.

The panel's position output drifts 12-22 CSS px during a stationary press.
Chromium's touch slop is 8 CSS px, so a tap that drifts past that is turned
into a scroll and never becomes a click - which is why a tapped button only
sometimes activates. These tests pin the fix: no motion is reported until the
press commits to a real drag.
"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("tf", sys.argv[1] if len(sys.argv) > 1 else "/tmp/tf-v4.py")
tf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tf)

NOW = [1000.0]
tf.time.monotonic = lambda: NOW[0]

RAW_PER_CSS_X = 16384 / 1536.0
RAW_PER_CSS_Y = 9600 / 960.0


class FakeUI:
    def __init__(self):
        self.written = []

    def write_event(self, e):
        self.written.append((tf.ecodes.bytype.get(e.type, {}).get(e.code, e.code), e.value))

    def take(self):
        w, self.written = self.written, []
        return w


def down(tid, cx, cy, slot=0):
    return [(tf.EV_ABS, tf.SLOT, slot), (tf.EV_ABS, tf.TID, tid),
            (tf.EV_ABS, tf.POS_X, int(cx * RAW_PER_CSS_X)),
            (tf.EV_ABS, tf.POS_Y, int(cy * RAW_PER_CSS_Y))]


def move(cx, cy, slot=0):
    return [(tf.EV_ABS, tf.SLOT, slot),
            (tf.EV_ABS, tf.POS_X, int(cx * RAW_PER_CSS_X)),
            (tf.EV_ABS, tf.POS_Y, int(cy * RAW_PER_CSS_Y))]


def up(slot=0):
    return [(tf.EV_ABS, tf.SLOT, slot), (tf.EV_ABS, tf.TID, -1)]


def reported_positions(events):
    """Reconstruct the (css_x, css_y) the virtual device would report."""
    xs, ys, cur = [], [], None
    for n, v in events:
        if n == "ABS_MT_POSITION_X":
            cur = ("x", v)
        elif n == "ABS_MT_POSITION_Y":
            cur = ("y", v)
    # collect per-axis writes in order
    ax = [v for n, v in events if n == "ABS_MT_POSITION_X"]
    ay = [v for n, v in events if n == "ABS_MT_POSITION_Y"]
    return ([round(v / RAW_PER_CSS_X) for v in ax],
            [round(v / RAW_PER_CSS_Y) for v in ay])


failures = []


def check(label, cond, detail=""):
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s  %r" % (label, detail))
        failures.append(label)


# --------------------------------------------------------------------------
print("1. the measured failing tap: y drifts 687 -> 693 (6px), x 550 -> 544")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2, 5.0, 300)
f.process(down(1200, 550, 687))
ui.take()
for cx, cy in [(549, 688), (546, 689), (545, 690), (544, 691), (544, 693)]:
    f.process(move(cx, cy))
xs, ys = reported_positions(ui.take())
check("no X motion reported", xs == [], xs)
check("no Y motion reported", ys == [], ys)

# --------------------------------------------------------------------------
print("2. the worst measured drift: y 673 -> 695 (22px), x steady")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2, 5.0, 300)
f.process(down(1201, 543, 673))
ui.take()
for cy in [676, 677, 679, 680, 684, 691, 695, 695, 695]:
    f.process(move(542, cy))
xs, ys = reported_positions(ui.take())
check("22px of drift still reports no motion", xs == [] and ys == [], (xs, ys))
check("contact was not committed", f.meta[1201]["committed"] is False)

# --------------------------------------------------------------------------
print("3. a real drag commits and then reports faithfully")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2, 5.0, 300)
f.process(down(1202, 200, 500))
ui.take()
# travel: 25 CSS px is 267 raw units, still short of the 300 commit point
f.process(move(225, 500))
check("still silent below the commit threshold", reported_positions(ui.take())[0] == [])
f.process(move(245, 500))
xs, _ = reported_positions(ui.take())
check("committed past the threshold", xs != [], xs)
check("committed flag set", f.meta[1202]["committed"] is True)
for cx in [250, 260, 270]:
    f.process(move(cx, 500))
xs, _ = reported_positions(ui.take())
check("subsequent motion passes through", xs == [250, 260, 270], xs)

# --------------------------------------------------------------------------
print("4. a drag keeps working after commit even if it slows right down")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2, 5.0, 300)
f.process(down(1203, 200, 500))
f.process(move(250, 500))
ui.take()
f.process(move(251, 500))
f.process(move(252, 500))
xs, _ = reported_positions(ui.take())
check("slow residual motion still reported once committed", xs == [251, 252], xs)

# --------------------------------------------------------------------------
print("5. press point is what gets reported while uncommitted")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2, 5.0, 300)
f.process(down(1204, 400, 300))
ev = ui.take()
ax = [v for n, v in ev if n == "ABS_X"]
check("initial ABS_X is the press point", ax and round(ax[0] / RAW_PER_CSS_X) == 400, ax)
for cy in [305, 310, 315]:
    f.process(move(400, cy))
ev = ui.take()
ax = [v for n, v in ev if n == "ABS_X"]
check("ABS_X does not drift before commit", all(round(v / RAW_PER_CSS_X) == 400 for v in ax), ax)

print()
if failures:
    print("FAILURES: %s" % failures)
    sys.exit(1)
print("all drift scenarios passed")
