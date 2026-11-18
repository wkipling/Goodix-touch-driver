"""Storm scenarios that turn a single touch into a stuck press.

The symptom under test: the page sees a press that never ends. A press that
never ends is exactly what Home Assistant concludes is a hold.
"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("tf", sys.argv[1] if len(sys.argv) > 1 else "/tmp/touch-filter-v2.py")
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


def down(tid, slot=0, x=2000, y=800):
    return [(tf.EV_ABS, tf.SLOT, slot), (tf.EV_ABS, tf.TID, tid),
            (tf.EV_ABS, tf.POS_X, x), (tf.EV_ABS, tf.POS_Y, y)]


def up(slot=0):
    return [(tf.EV_ABS, tf.SLOT, slot), (tf.EV_ABS, tf.TID, -1)]


def btn_state(events, state):
    """Track BTN_TOUCH across the emitted event list."""
    for n, v in events:
        if n == "BTN_TOUCH":
            state = v
    return state


failures = []


def check(label, cond, detail=""):
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s  %r" % (label, detail))
        failures.append(label)


# --------------------------------------------------------------------------
print("A. phantom left behind after the finger lifts must not start a press")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2)
btn = 0
btn = btn_state(ui.take(), btn)

f.process(down(900, slot=0))          # real finger
btn = btn_state(ui.take(), btn)
advance(20)
f.process(down(901, slot=1, x=2002, y=810))   # phantom lands mid-press
btn = btn_state(ui.take(), btn)
advance(100)
f.process(up(slot=0))                 # finger lifts; phantom still "down"
ev = ui.take()
btn = btn_state(ev, btn)
check("finger release is reported", ("ABS_MT_TRACKING_ID", -1) in ev, ev)
check("BTN_TOUCH went up with the finger", btn == 0, btn)
for _ in range(20):                   # 400ms of silence afterwards
    advance(20)
    f.process([])
    btn = btn_state(ui.take(), btn)
check("no press resurrected from the leftover phantom", btn == 0, btn)

# --------------------------------------------------------------------------
print("B. a genuinely stuck panel contact is force-released")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2)
btn = 0
f.process(down(950, slot=0))
btn = btn_state(ui.take(), btn)
held_ms = 0
for _ in range(400):                  # 8 seconds with no motion at all
    advance(20)
    held_ms += 20
    f.process([])
    ev = ui.take()
    if ev:
        btn = btn_state(ev, btn)
check("stuck contact eventually released", btn == 0, "still down after %dms" % held_ms)

# --------------------------------------------------------------------------
print("C. a real long press is NOT cut short by the stuck guard")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2)
btn = 0
f.process(down(960, slot=0))
btn = btn_state(ui.take(), btn)
for i in range(60):                   # 1200ms, a normal hold-for-more-info
    advance(20)
    f.process([])
    btn = btn_state(ui.take(), btn)
check("press survives 1.2s", btn == 1, btn)

# --------------------------------------------------------------------------
print("D. second finger still works: promoted when born and kept when alone")
ui = FakeUI()
f = tf.Filter(ui, 0.200, 2)
f.process(down(970, slot=0))
ui.take()
advance(10)
f.process(down(971, slot=1, x=3000, y=3000))
ui.take()
for _ in range(15):
    advance(20)
    f.process([])
ev = ui.take()
check("second contact promoted after the window", 971 in [v for n, v in ev if n == "ABS_MT_TRACKING_ID"] or 971 in f.emitted.values())

print()
if failures:
    print("FAILURES: %s" % failures)
    sys.exit(1)
print("all storm scenarios passed")
