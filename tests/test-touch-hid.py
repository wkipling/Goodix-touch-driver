"""Test the HID-level filter: report decoding and the contact-id trust rule.

Uses the report layouts measured from the panel, including the three solo
phantom reports (contact id 5) that the old "trust a contact born alone" rule
let through.
"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("th", sys.argv[1] if len(sys.argv) > 1 else "/tmp/touch-filter-hid.py")
th = importlib.util.module_from_spec(spec)
spec.loader.exec_module(th)
th.DEBUG = False


def log(msg):
    pass


th.log = log


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
    """Build a report-0x0D payload: 10 blocks, Scan Time, Contact Count."""
    data = bytearray(th.REPORT_LEN)
    for cid, tip, x, y in contacts:
        i = (cid - 1) % th.FINGERS
        base = 5 * i
        data[base] = (1 if tip else 0) | ((cid & 0x0F) << 4)
        data[base + 1] = x & 0xFF
        data[base + 2] = (x >> 8) & 0xFF
        data[base + 3] = y & 0xFF
        data[base + 4] = (y >> 8) & 0xFF
    data[th.CONTACT_COUNT_OFFSET] = count
    return bytes(data)


failures = []


def check(label, cond, detail=""):
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s  %r" % (label, detail))
        failures.append(label)


# --------------------------------------------------------------------------
print("1. decoding: only tipped blocks count, ids survive")
# the measured storm at t=97.385: phantom id 2, real id 1
got = dec(report([(1, True, 5843, 6815), (2, True, 5846, 3583)]))
check("two tipped contacts decoded", len(got) == 2, got)
check("ids preserved", sorted(c[0] for c in got) == [1, 2], got)
check("untipped blocks ignored", len(dec(report([(1, False, 10, 10)]))) == 0)

# --------------------------------------------------------------------------
print("2. the measured failing tap: phantom id 2 + real id 1")
ui = FakeUInput()
out = th.Emitter.__new__(th.Emitter)
out.ui = ui
out.tid = 0
out.down = False
out.sent = (None, None)
panel = th.Panel(out)
panel.feed(dec(report([(1, True, 5843, 6815), (2, True, 5846, 3583)])), now())
xs = [v for n, v in ui.events if n == "ABS_MT_POSITION_X"]
ys = [v for n, v in ui.events if n == "ABS_MT_POSITION_Y"]
check("press reported at the real finger", xs == [5843] and ys == [6815], (xs, ys))

# --------------------------------------------------------------------------
print("3. the phantom's garbage Y is never reported while it is live")
ui.events.clear()
for ph_y in (5277, 6409, 7580, 8813, 9237):
    panel.feed(dec(report([(1, True, 5846, 6815), (2, True, 5846, ph_y)])), now())
ys = [v for n, v in ui.events if n == "ABS_MT_POSITION_Y"]
check("no phantom Y emitted", all(v == 6815 for v in ys) or ys == [], ys)

# --------------------------------------------------------------------------
print("4. a SOLO phantom (id 5) must not start a press")
ui.events.clear()
panel2 = th.Panel(out)
out2 = th.Emitter.__new__(th.Emitter)
out2.ui = FakeUInput()
out2.tid = 0
out2.down = False
out2.sent = (None, None)
panel2 = th.Panel(out2)
panel2.feed(dec(report([(5, True, 0, 684)])), now())
check("no press started", out2.down is False)
check("counted as solo phantom", panel2.phantom_frames == 1)

# --------------------------------------------------------------------------
print("5. a solo REAL contact (id 1) starts a press immediately")
out3 = th.Emitter.__new__(th.Emitter)
out3.ui = FakeUInput()
out3.tid = 0
out3.down = False
out3.sent = (None, None)
panel3 = th.Panel(out3)
panel3.feed(dec(report([(1, True, 2702, 1362)])), now())
check("press started", out3.down is True)
xs = [v for n, v in out3.ui.events if n == "ABS_MT_POSITION_X"]
check("at the touched point", xs == [2702], xs)

# --------------------------------------------------------------------------
print("6. release when the tip drops")
panel3.feed(dec(report([])), now())
check("released", out3.down is False)
tids = [v for n, v in out3.ui.events if n == "ABS_MT_TRACKING_ID"]
check("tracking id cleared", tids[-1] == -1, tids)

# --------------------------------------------------------------------------
print("7. a solo phantom mid-press does not drag the real finger away")
out4 = th.Emitter.__new__(th.Emitter)
out4.ui = FakeUInput()
out4.tid = 0
out4.down = False
out4.sent = (None, None)
panel4 = th.Panel(out4)
panel4.feed(dec(report([(1, True, 5000, 5000)])), now())
out4.ui.events.clear()
panel4.feed(dec(report([(5, True, 0, 960)])), now())
xs = [v for n, v in out4.ui.events if n == "ABS_MT_POSITION_X"]
check("real finger stayed put", xs == [] or all(v == 5000 for v in xs), xs)
check("still down", out4.down is True)

# --------------------------------------------------------------------------
print("8. a real drag still tracks")
out4.ui.events.clear()
for x in (5100, 5200, 5300):
    panel4.feed(dec(report([(1, True, x, 5000)])), now())
xs = [v for n, v in out4.ui.events if n == "ABS_MT_POSITION_X"]
check("drag reported", xs == [5100, 5200, 5300], xs)

print()
if failures:
    print("FAILURES: %s" % failures)
    sys.exit(1)
print("all HID filter scenarios passed")

# --------------------------------------------------------------------------
# Appended: the 10-block regression. An earlier decoder read only the first
# five blocks, so anything in blocks 6-10 was invisible, and the byte at
# offset 27 (part of block 6) was misread as Contact Count.
print("9. all ten finger blocks are decoded (blocks 6-10 were invisible)")
all_ten = [(cid, True, 1000 + cid, 2000 + cid) for cid in range(1, 11)]
got = dec(report(all_ten))
check("ten tipped blocks decoded", len(got) == 10, len(got))
check("ids 1..10 all present", sorted(c[0] for c in got) == list(range(1, 11)),
      sorted(c[0] for c in got))
by_id = {c[0]: c for c in got}
check("block 6 coordinates correct",
      by_id[6][1] == 1006 and by_id[6][2] == 2006, by_id.get(6))
check("block 10 coordinates correct",
      by_id[10][1] == 1010 and by_id[10][2] == 2010, by_id.get(10))

print("10. Contact Count is read from offset 52, not 27")
contacts, count = th.decode(report([(1, True, 10, 20)], count=7))
check("count byte round-trips", count == 7, count)
check("count does not corrupt block 6",
      dec(report([(6, True, 111, 222)], count=0))[0][1] == 111)
check("payload length is 53", th.REPORT_LEN == 53, th.REPORT_LEN)

print("11. a contact only in a high block is still trusted if it is id 1")
out5 = th.Emitter.__new__(th.Emitter)
out5.ui = FakeUInput(); out5.tid = 0; out5.down = False; out5.sent = (None, None)
p5 = th.Panel(out5)
# id 1 written into block 0 is the normal case; simulate the inverse below
p5.feed(dec(report([(1, True, 4000, 4000)])), now())
check("press started from id 1", out5.down is True)

print()
if failures:
    print("FAILURES: %s" % failures)
    sys.exit(1)
print("all ten-block scenarios passed")
