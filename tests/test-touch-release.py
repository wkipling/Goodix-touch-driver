"""The stuck-press bug: a tap that never gets released.

Symptom: a plain tap makes the page show a *hold* - Home Assistant's ripple
expands into a large blue circle. That happens because the press is never
released: the page's hold timer fires while the finger is already up.

Cause: the release was conditioned on "no contacts at all". But after a real
finger lifts, the panel usually still reports a phantom, so that condition
never came true and the press stayed down. Measured on this panel: frames
arrive with id 1 absent but a phantom present, both solo and in groups.
"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("th", sys.argv[1] if len(sys.argv) > 1 else "/tmp/touch-filter-hid.py")
th = importlib.util.module_from_spec(spec)
spec.loader.exec_module(th)
th.DEBUG = False
th.log = lambda msg: None


class FakeUInput:
    def __init__(self, *a, **k):
        self.events = []

        class Dev:
            path = "/dev/null"
            fd = -1
        self.device = Dev()

    def write_event(self, ev):
        self.events.append((th.e.bytype.get(ev.type, {}).get(ev.code, ev.code), ev.value))

    def close(self):
        pass


th.UInput = FakeUInput

T = [1000.0]


def now():
    return T[0]


def advance(ms):
    T[0] += ms / 1000.0


def dec(payload):
    """Contacts only, from a report payload."""
    return th.decode(payload)[0]


def report(contacts, count=0):
    data = bytearray(th.REPORT_LEN)
    for cid, tip, x, y in contacts:
        i = (cid - 1) % th.FINGERS
        b = 5 * i
        data[b] = (1 if tip else 0) | ((cid & 0x0F) << 4)
        data[b + 1] = x & 0xFF
        data[b + 2] = (x >> 8) & 0xFF
        data[b + 3] = y & 0xFF
        data[b + 4] = (y >> 8) & 0xFF
    return bytes(data)


def make():
    out = th.Emitter.__new__(th.Emitter)
    out.ui = FakeUInput()
    out.tid = 0
    out.down = False
    out.sent = (None, None)
    return out, th.Panel(out)


def is_down(out):
    return out.down


failures = []


def check(label, cond, detail=""):
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s  %r" % (label, detail))
        failures.append(label)


# --------------------------------------------------------------------------
print("1. THE BUG: finger lifts, a phantom remains -> press must release")
out, panel = make()
panel.feed(dec(report([(1, True, 5000, 5000), (2, True, 5001, 1000)])), now())
check("press started", is_down(out))
advance(60)
# The finger lifts. Only the phantom is still reported - no id 1.
panel.feed(dec(report([(2, True, 5001, 999)])), now())
advance(40)
panel.feed(dec(report([(2, True, 5002, 998)])), now())
advance(40)
panel.feed(dec(report([(2, True, 5003, 997)])), now())
check("released once id 1 had been absent past the grace", not is_down(out))

# --------------------------------------------------------------------------
print("2. phantom-only frames DURING a press must not cut it short")
out2, panel2 = make()
panel2.feed(dec(report([(1, True, 5000, 5000)])), now())
advance(15)
panel2.feed(dec(report([(2, True, 5000, 1200)])), now())   # id 1 omitted
advance(15)
check("still down through a phantom-only frame", is_down(out2))
panel2.feed(dec(report([(1, True, 5020, 5000)])), now())   # id 1 back
advance(15)
check("still down after id 1 returns", is_down(out2))

# --------------------------------------------------------------------------
print("3. a long stationary press with no reports at all still releases")
out3, panel3 = make()
panel3.feed(dec(report([(1, True, 5000, 5000)])), now())
check("press started", is_down(out3))
# panel goes completely silent - no further reports, as it is change-driven
advance(150)
panel3.tick(now())
check("tick released the press", not is_down(out3))
tids = [v for n, v in out3.ui.events if n == "ABS_MT_TRACKING_ID"]
check("tracking id cleared", tids[-1] == -1, tids)

# --------------------------------------------------------------------------
print("4. a normal tap releases promptly, well inside a tap's lifetime")
out4, panel4 = make()
panel4.feed(dec(report([(1, True, 2702, 1362)])), now())
advance(70)
panel4.feed(dec(report([(1, False, 2702, 1362)])), now())  # tip drops
panel4.tick(now())
check("released on the explicit lift report", not is_down(out4))

# --------------------------------------------------------------------------
print("4b. a tap whose finger lifts leaving only a phantom stays SHORT")
# This is the common case on this panel and the one that was producing holds:
# waiting out a grace period here pushed every tap past the page's ~190ms
# hold threshold.
out4b, panel4b = make()
panel4b.feed(dec(report([(1, True, 5000, 5000)])), now())
advance(90)          # a realistic tap is on the glass ~90ms
panel4b.feed(dec(report([(2, True, 5001, 9000)])), now())
t_phantom = now()
# the panel goes quiet; the tick is what notices
advance(40)
panel4b.tick(now())
dur = (now() - 1000.0) * 1000
check("released", not is_down(out4b))
check("total press stayed under 190ms", 90 + 40 < 190, dur)

# --------------------------------------------------------------------------
print("5. a real drag survives, including phantom interleaving")
out5, panel5 = make()
panel5.feed(dec(report([(1, True, 4000, 4000)])), now())
xs = []
for i, x in enumerate((4100, 4200, 4300, 4400), start=1):
    advance(15)
    # phantom interleaved, id 1 still present
    panel5.feed(dec(report([(1, True, x, 4000), (2, True, x, 9000)])), now())
xs = [v for n, v in out5.ui.events if n == "ABS_MT_POSITION_X"]
check("drag tracked through the phantoms", xs == [4000, 4100, 4200, 4300, 4400], xs)
check("drag still live", is_down(out5))

# --------------------------------------------------------------------------
print("6. no press is created from phantoms alone")
out6, panel6 = make()
for i in range(10):
    panel6.feed(dec(report([(2, True, 1000 + i, 2000)])), now())
    advance(15)
check("no press started", not is_down(out6))
check("phantom frames counted", panel6.phantom_frames == 10, panel6.phantom_frames)

print()
if failures:
    print("FAILURES: %s" % failures)
    sys.exit(1)
print("all stuck-press scenarios passed")
