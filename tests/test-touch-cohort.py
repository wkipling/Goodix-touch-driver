"""Storm-cohort scenario, reconstructed from the panel's own HID reports.

Measured on this panel (/tmp/dual.log): a single tap arrives as a cohort of
2-6 contacts born in one report. The phantoms occupy the early blocks with the
real finger's X and a garbage Y; the real finger is the LAST block. In 216 of
223 storm frames the real contact was block 4 (highest slot), phantoms ahead.

Before the fix the filter trusted whichever contact arrived first - the
phantom - forwarded it, and deferred the real finger, which then died unsent.
Chromium received a contact at the real X and a garbage Y: a touch that
teleported down the screen, which scrolls the card and cancels the tap.
"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("tf", sys.argv[1] if len(sys.argv) > 1 else "/tmp/tf-v5.py")
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
        self.written.append((tf.ecodes.bytype.get(e.type, {}).get(e.code, e.code), e.value))

    def take(self):
        w, self.written = self.written, []
        return w


def contact(slot, tid, x, y):
    return [(tf.EV_ABS, tf.SLOT, slot), (tf.EV_ABS, tf.TID, tid),
            (tf.EV_ABS, tf.POS_X, x), (tf.EV_ABS, tf.POS_Y, y)]


def up(slot):
    return [(tf.EV_ABS, tf.SLOT, slot), (tf.EV_ABS, tf.TID, -1)]


def downs(events):
    return [v for n, v in events if n == "ABS_MT_TRACKING_ID" and v != -1]


failures = []


def check(label, cond, detail=""):
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s  %r" % (label, detail))
        failures.append(label)


# --------------------------------------------------------------------------
print("1. the real failing tap: phantom in slot 0, real finger in slot 1")
# From /tmp/dual.log at t=97.385 - phantom id 2 (5846, 3583), real id 1 (5843, 6815)
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2, 5.0)
f.process(contact(0, 900, 5846, 3583) + contact(1, 901, 5843, 6815))
got = downs(ui.take())
check("only the real finger is forwarded", got == [901], got)
check("phantom was not forwarded", 900 not in got)

# --------------------------------------------------------------------------
print("2. the phantom's garbage Y must never reach Chromium")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2, 5.0)
f.process(contact(0, 910, 5846, 3583) + contact(1, 911, 5843, 6815))
ui.take()
for ph_y in (5277, 6409, 7580, 8813, 9237):     # the measured sweep
    advance(10)
    f.process([(tf.EV_ABS, tf.SLOT, 0), (tf.EV_ABS, tf.POS_X, 5846),
               (tf.EV_ABS, tf.POS_Y, ph_y)])
ev = ui.take()
ys = [v for n, v in ev if n == "ABS_MT_POSITION_Y"]
check("no phantom Y positions emitted", ys == [], ys)
check("real contact still the only one emitted", f.emitted == {1: 911}, f.emitted)

# --------------------------------------------------------------------------
print("3. the real finger's own tap completes normally")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2, 5.0)
f.process(contact(0, 920, 5846, 3583) + contact(1, 921, 5843, 6815))
ui.take()
advance(60)
f.process(up(1))
ev = ui.take()
check("real finger released", ("ABS_MT_TRACKING_ID", -1) in ev, ev)
check("no press survives", f.emitted == {}, f.emitted)

# --------------------------------------------------------------------------
print("4. a six-contact storm still yields exactly one real contact")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2, 5.0)
batch = []
# blocks 0..4 phantoms with the real X and garbage Ys, block 5 the real finger
for i, py in enumerate([6017, 2931, 4284, 960, 4943]):
    batch += contact(i, 930 + i, 6235, py)
batch += contact(5, 999, 6235, 6933)
f.process(batch)
got = downs(ui.take())
check("one contact forwarded", len(got) == 1, got)
check("it is the last block (the real finger)", got == [999], got)

# --------------------------------------------------------------------------
print("5. an ordinary tap with no phantoms is unaffected")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2, 5.0)
f.process(contact(0, 950, 2702, 1362))
got = downs(ui.take())
check("forwarded immediately", got == [950], got)
advance(90)
f.process(up(0))
check("released cleanly", f.emitted == {})

# --------------------------------------------------------------------------
print("6. a genuine second finger in a later frame is still promoted")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2, 5.0)
f.process(contact(0, 960, 1000, 1000))
ui.take()
advance(10)
f.process(contact(1, 961, 3000, 3000))
ui.take()
for _ in range(25):
    advance(10)
    f.process([])
got = downs(ui.take())
check("second contact promoted by age", 961 in got or 961 in f.emitted.values(), got)

print()
if failures:
    print("FAILURES: %s" % failures)
    sys.exit(1)
print("all cohort scenarios passed")
