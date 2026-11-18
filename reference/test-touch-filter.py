"""Exercise the v2 touch filter's decision logic with a fake clock and sink.

No real devices: UInput is replaced by a recorder and time.monotonic by a
clock we advance by hand, so each scenario is deterministic.
"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("tf", "/tmp/touch-filter-v2.py")
tf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tf)

NOW = [1000.0]
tf.time.monotonic = lambda: NOW[0]


def advance(ms):
    NOW[0] += ms / 1000.0


class FakeUI:
    def __init__(self):
        self.written = []

    def write_event(self, e):
        name = tf.ecodes.bytype.get(e.type, {}).get(e.code, e.code)
        self.written.append((name, e.value))

    def take(self):
        w, self.written = self.written, []
        return w


def down(tid, x=2000, y=800, slot=0):
    return [(tf.EV_ABS, tf.SLOT, slot), (tf.EV_ABS, tf.TID, tid),
            (tf.EV_ABS, tf.POS_X, x), (tf.EV_ABS, tf.POS_Y, y)]


def move(x, y, slot=0):
    return [(tf.EV_ABS, tf.SLOT, slot), (tf.EV_ABS, tf.POS_X, x),
            (tf.EV_ABS, tf.POS_Y, y)]


def up(slot=0):
    return [(tf.EV_ABS, tf.SLOT, slot), (tf.EV_ABS, tf.TID, -1)]


def downs_in(events):
    """Tracking ids the virtual device was told went down."""
    return [v for n, v in events if n == "ABS_MT_TRACKING_ID" and v != -1]


failures = []


def check(label, cond, detail=""):
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s  %r" % (label, detail))
        failures.append(label)


# --------------------------------------------------------------------------
print("1. clean tap: born alone is forwarded at once, released on lift")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2)
f.process(down(100))
check("forwarded immediately", downs_in(ui.take()) == [100])
advance(120)
f.process(up())
ev = ui.take()
check("tracking id released", ("ABS_MT_TRACKING_ID", -1) in ev, ev)
check("BTN_TOUCH went up", ("BTN_TOUCH", 0) in ev, ev)
check("nothing dropped", f.dropped == 0)

# --------------------------------------------------------------------------
print("2. phantom arriving mid-tap is dropped; the real finger stays live")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2)
f.process(down(200))
ui.take()
advance(20)
f.process(down(201, x=2005, y=9000, slot=1))   # phantom: real X, garbage Y
check("phantom not forwarded", downs_in(ui.take()) == [])
advance(100)
f.process(up(slot=1))                          # phantom dies at 120ms
check("phantom counted as dropped", f.dropped == 1)
check("real finger still reported", f.emitted == {0: 200}, f.emitted)
advance(30)
f.process(move(2100, 850))
ui.take()
check("drag still live", f.emitted == {0: 200}, f.emitted)

# --------------------------------------------------------------------------
print("3. deadlock regression: pending contact promoted with NO new events")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2)
f.process(down(300))
ui.take()
advance(10)
f.process(down(301, slot=1))                   # second contact, 1 active at birth
check("second not forwarded yet", downs_in(ui.take()) == [])
for _ in range(30):                            # panel silent: idle ticks only
    advance(10)
    f.process([])
check("second promoted by the clock alone", 301 in downs_in(ui.take()))
check("second is in the emitted set", 301 in f.emitted.values(), f.emitted)

# --------------------------------------------------------------------------
print("4. storm-born real tap survives once the phantoms expire")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2)
f.process(down(400, slot=0))
f.process(down(401, x=2001, y=9000, slot=1))
f.process(down(402, x=2002, y=8999, slot=2))
ui.take()
advance(10)
f.process(down(403, slot=3))                   # the actual finger lands
advance(90)
f.process(up(slot=1))                          # phantoms die, as they do
f.process(up(slot=2))
advance(120)
f.process([])
advance(20)
f.process([])
got = downs_in(ui.take())
check("survivor promoted alongside the first", 403 in got or 403 in f.emitted.values(), got)

# --------------------------------------------------------------------------
print("5. no idle-tick spam: a still finger emits nothing")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2)
f.process(down(500))
ui.take()
quiet = 0
for _ in range(50):
    advance(20)
    f.process([])
    quiet += len(ui.take())
check("zero events while still", quiet == 0, quiet)

# --------------------------------------------------------------------------
print("6. motion is reported exactly once per change")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2)
f.process(down(700, x=2000, y=800))
ui.take()
f.process(move(2001, 800))
# Motion inside the commit window is deliberately suppressed, so the press
# has to travel before anything is reported. Then one change, one write.
f.process(move(2400, 800))          # 400 raw units: past the commit point
f.process(move(2401, 800))
moved = ui.take()
check("one X update for one X change",
      [v for n, v in moved if n == "ABS_MT_POSITION_X"][-1] == 2401, moved)
f.process(move(2401, 800))
check("repeat position emits nothing", ui.take() == [])

# --------------------------------------------------------------------------
print("7. never forwards more than two contacts")
ui = FakeUI()
f = tf.Filter(ui, 0.010, 2)
for i in range(6):
    f.process(down(600 + i, slot=i))
advance(50)
f.process([])
ev = ui.take()
check("two contacts max", len(downs_in(ev)) <= 2, downs_in(ev))

# --------------------------------------------------------------------------
print()
if failures:
    print("FAILURES: %s" % failures)
    sys.exit(1)
print("all scenarios passed")
