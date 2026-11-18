"""Extra tests for the rewritten driver's own guarantees.

These complement test-touch-hid.py and test-touch-release.py, which encode
the measured firmware behaviour.  Here the focus is on what the rewrite adds:

  * the noise floor is part of the absinfo at creation (not EVIOCSABS),
  * short reports are decoded tolerantly instead of killing touch,
  * unchanged coordinates emit nothing,
  * a userspace deadband is used only if the kernel fuzz did not stick,
  * each press gets a fresh tracking id, and BTN_TOUCH follows it.

Run:  python3 test-touch-driver.py ./goodix-touch-driver.py
"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location(
    "gd", sys.argv[1] if len(sys.argv) > 1 else "/tmp/goodix-touch-driver.py")
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)
gd.DEBUG = False
gd.log = lambda msg: None


class FakeUInput:
    def __init__(self, *a, **k):
        self.events = []

        class Dev:
            path = None
            fd = -1
        self.device = Dev()

    def write_event(self, ev):
        self.events.append(
            (gd.e.bytype.get(ev.type, {}).get(ev.code, ev.code), ev.value))

    def close(self):
        pass


gd.UInput = FakeUInput

NOW = [1000.0]


def now():
    return NOW[0]


def advance(ms):
    NOW[0] += ms / 1000.0


def report(contacts, count=0, length=None):
    data = bytearray(length if length is not None else gd.REPORT_LEN)
    for cid, tip, x, y in contacts:
        base = 5 * ((cid - 1) % gd.FINGERS)
        if base + 5 > len(data):
            continue
        data[base] = (1 if tip else 0) | ((cid & 0x0F) << 4)
        data[base + 1] = x & 0xFF
        data[base + 2] = (x >> 8) & 0xFF
        data[base + 3] = y & 0xFF
        data[base + 4] = (y >> 8) & 0xFF
    if len(data) > gd.CONTACT_COUNT_OFFSET:
        data[gd.CONTACT_COUNT_OFFSET] = count
    return bytes(data)


def dec(payload):
    return gd.decode(payload)[0]


def make(**kwargs):
    out = gd.Emitter.__new__(gd.Emitter)
    out.ui = FakeUInput()
    out.tid = 0
    out.down = False
    out.sent = (None, None)
    return out, gd.Panel(out, **kwargs)


def names(events, code):
    return [v for n, v in events if n == code]


failures = []


def check(label, cond, detail=""):
    if cond:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s  %r" % (label, detail))
        failures.append(label)


# --------------------------------------------------------------------------
print("1. the noise floor is in the absinfo handed to UInput")
caps = gd.abs_caps()
by_code = dict(caps[gd.e.EV_ABS])
for code in (gd.e.ABS_X, gd.e.ABS_Y,
             gd.e.ABS_MT_POSITION_X, gd.e.ABS_MT_POSITION_Y):
    check("%s fuzz=%d" % (gd.e.ABS[code], gd.FUZZ),
          by_code[code].fuzz == gd.FUZZ, by_code[code])
check("MT slot and tracking id keep fuzz 0",
      by_code[gd.e.ABS_MT_SLOT].fuzz == 0
      and by_code[gd.e.ABS_MT_TRACKING_ID].fuzz == 0)

# --------------------------------------------------------------------------
print("2. a short report is decoded, not rejected")
short = report([(1, True, 111, 222)], length=30)   # six whole blocks
contacts, count = gd.decode(short)
check("contact from a short payload decoded", contacts == [(1, 111, 222)],
      contacts)
check("count is None when the payload is too short", count is None, count)
check("empty payload is safe", gd.decode(b"") == ([], None))

# --------------------------------------------------------------------------
print("3. unchanged coordinates emit nothing")
out, panel = make()
panel.feed(dec(report([(1, True, 5000, 4000)])), now())
out.ui.events.clear()
for _ in range(5):
    panel.feed(dec(report([(1, True, 5000, 4000)])), now())
check("no motion events", out.ui.events == [], out.ui.events)

# --------------------------------------------------------------------------
print("4. changed coordinates emit once per axis")
panel.feed(dec(report([(1, True, 5100, 4000)])), now())
check("only X moved", names(out.ui.events, "ABS_MT_POSITION_X") == [5100]
      and names(out.ui.events, "ABS_MT_POSITION_Y") == [],
      out.ui.events)

# --------------------------------------------------------------------------
print("5. the in-process noise floor suppresses sub-fuzz motion")
out2, panel2 = make()
panel2.feed(dec(report([(1, True, 5000, 4000)])), now())
out2.ui.events.clear()
panel2.feed(dec(report([(1, True, 5020, 4000)])), now())   # 20 < FUZZ
check("sub-fuzz motion suppressed", out2.ui.events == [], out2.ui.events)
panel2.feed(dec(report([(1, True, 5100, 4000)])), now())   # 80 >= FUZZ
check("motion past fuzz emitted",
      names(out2.ui.events, "ABS_MT_POSITION_X") == [5100],
      out2.ui.events)

# --------------------------------------------------------------------------
print("5b. a few units of wobble never reach the event stream")
out2b, panel2b = make()
panel2b.feed(dec(report([(1, True, 5000, 5000)])), now())
out2b.ui.events.clear()
for x, y in ((5003, 5000), (5000, 5006), (4996, 5002), (5004, 4998)):
    panel2b.feed(dec(report([(1, True, x, y)])), now())
check("no motion from wobble", out2b.ui.events == [], out2b.ui.events)

# --------------------------------------------------------------------------
print("6. each press gets a fresh tracking id and BTN_TOUCH follows it")
out3, panel3 = make()
panel3.feed(dec(report([(1, True, 100, 100)])), now())
first_tids = names(out3.ui.events, "ABS_MT_TRACKING_ID")
check("BTN_TOUCH down", names(out3.ui.events, "BTN_TOUCH") == [1])
panel3.feed(dec(report([])), now())
check("BTN_TOUCH up", names(out3.ui.events, "BTN_TOUCH") == [1, 0])
panel3.feed(dec(report([(1, True, 200, 200)])), now())
tids = names(out3.ui.events, "ABS_MT_TRACKING_ID")
check("second press has a new tracking id",
      len([t for t in tids if t != -1]) == 2 and first_tids[0] != tids[-1],
      tids)

# --------------------------------------------------------------------------
print("7. a press on id 1 in the last block is still trusted")
payload = bytearray(gd.REPORT_LEN)
base = 5 * 9
payload[base] = 0x01 | (1 << 4)
payload[base + 1] = 0x34
payload[base + 2] = 0x12
payload[base + 3] = 0x78
payload[base + 4] = 0x56
contacts, _ = gd.decode(bytes(payload))
check("high-block id 1 decoded", contacts == [(1, 0x1234, 0x5678)], contacts)
out4, panel4 = make()
panel4.feed(contacts, now())
check("high-block id 1 starts a press", out4.down is True)

# --------------------------------------------------------------------------
print("8. an empty report releases immediately, with no grace")
out5, panel5 = make()
panel5.feed(dec(report([(1, True, 300, 300)])), now())
panel5.feed(dec(report([])), now())
check("released at once", out5.down is False)

# --------------------------------------------------------------------------
print("9. a press on the same column but another row, 53 ms later, is dropped")
out6, panel6 = make()
panel6.feed(dec(report([(1, True, 6240, 5575)])), now())
check("first press accepted", out6.down is True)
panel6.feed(dec(report([])), now())
check("first press released", out6.down is False)
advance(53)
panel6.feed(dec(report([(1, True, 6286, 7791)])), now())
check("ghost press suppressed", out6.down is False)
check("ghost counted once", panel6.ghost_presses == 1, panel6.ghost_presses)
check("only one press reported", panel6.presses == 1, panel6.presses)

# --------------------------------------------------------------------------
print("10. a fast second tap is kept unless it is the same column, other row")
out7, panel7 = make()
panel7.feed(dec(report([(1, True, 6240, 5575)])), now())
panel7.feed(dec(report([])), now())
advance(53)
panel7.feed(dec(report([(1, True, 6286, 5900)])), now())   # dY 325 < 400
check("nearby row kept", out7.down is True and panel7.presses == 2,
      panel7.presses)

out8, panel8 = make()
panel8.feed(dec(report([(1, True, 6240, 5575)])), now())
panel8.feed(dec(report([])), now())
advance(53)
panel8.feed(dec(report([(1, True, 9000, 9000)])), now())   # different column
check("different column kept", out8.down is True and panel8.presses == 2,
      panel8.presses)

out9, panel9 = make()
panel9.feed(dec(report([(1, True, 6240, 5575)])), now())
panel9.feed(dec(report([])), now())
advance(400)                                               # slow: a real tap
panel9.feed(dec(report([(1, True, 6286, 7791)])), now())
check("slow second tap kept", out9.down is True and panel9.presses == 2,
      panel9.presses)

# --------------------------------------------------------------------------
print("11. the guard can be switched off")
out10, panel10 = make()
panel10.guard = False
panel10.feed(dec(report([(1, True, 6240, 5575)])), now())
panel10.feed(dec(report([])), now())
advance(53)
panel10.feed(dec(report([(1, True, 6286, 7791)])), now())
check("guard off -> press accepted", out10.down is True)
check("guard off -> none dropped", panel10.ghost_presses == 0)

# --------------------------------------------------------------------------
print("12. the thresholds are inclusive, and the drag case is a known limit")

# gap: exactly at the threshold drops, one millisecond past keeps.  A 125 ms
# threshold is used so the arithmetic is exact in binary floating point.
out11, panel11 = make(ghost_gap_ms=125.0)
panel11.feed(dec(report([(1, True, 6240, 5575)])), now())
panel11.feed(dec(report([])), now())
advance(125)
panel11.feed(dec(report([(1, True, 6250, 8000)])), now())
check("gap == threshold drops", out11.down is False and
      panel11.ghost_presses == 1, panel11.ghost_presses)

out12, panel12 = make(ghost_gap_ms=125.0)
panel12.feed(dec(report([(1, True, 6240, 5575)])), now())
panel12.feed(dec(report([])), now())
advance(126)
panel12.feed(dec(report([(1, True, 6250, 8000)])), now())
check("gap > threshold keeps", out12.down is True and panel12.presses == 2,
      panel12.presses)

# dY: 400 exactly is a different row; 399 is the same row.
out13, panel13 = make()
panel13.feed(dec(report([(1, True, 6000, 5000)])), now())
panel13.feed(dec(report([])), now())
advance(50)
panel13.feed(dec(report([(1, True, 6000, 5400)])), now())
check("dY == 400 drops", out13.down is False and panel13.ghost_presses == 1,
      panel13.ghost_presses)

out14, panel14 = make()
panel14.feed(dec(report([(1, True, 6000, 5000)])), now())
panel14.feed(dec(report([])), now())
advance(50)
panel14.feed(dec(report([(1, True, 6000, 5399)])), now())
check("dY == 399 keeps", out14.down is True and panel14.presses == 2,
      panel14.presses)

# dX: 120 exactly is the same column; 121 is not.
out15, panel15 = make()
panel15.feed(dec(report([(1, True, 6000, 5000)])), now())
panel15.feed(dec(report([])), now())
advance(50)
panel15.feed(dec(report([(1, True, 6120, 5500)])), now())
check("dX == 120 drops", out15.down is False and panel15.ghost_presses == 1,
      panel15.ghost_presses)

out16, panel16 = make()
panel16.feed(dec(report([(1, True, 6000, 5000)])), now())
panel16.feed(dec(report([])), now())
advance(50)
panel16.feed(dec(report([(1, True, 6121, 5500)])), now())
check("dX == 121 keeps", out16.down is True and panel16.presses == 2,
      panel16.presses)

# Known limitation, deliberately left in place.  The reference is where the
# previous press *started*, so a drag that ends far from its origin can make an
# immediate follow-up tap look like a jump.  Comparing against the drag's end
# instead suppresses two ordinary presses (104 ms and 50 ms, both just after a
# swipe) in the captures, and the data cannot say which reading is right.  This
# test pins the conservative behaviour so a future change has to be deliberate;
# see the probe's --bases mode and docs/FINDINGS-ADDENDUM.md.
out17, panel17 = make()
panel17.feed(dec(report([(1, True, 6000, 1000)])), now())
panel17.feed(dec(report([(1, True, 6000, 9000)])), now())   # drag to the bottom
panel17.feed(dec(report([])), now())
advance(50)
panel17.feed(dec(report([(1, True, 6000, 8800)])), now())
check("known limitation: tap right after a vertical drag is dropped",
      out17.down is False and panel17.ghost_presses == 1,
      (out17.down, panel17.ghost_presses))

# --------------------------------------------------------------------------
print("13. a contact that keeps reporting but never moves is released")
# The firmware can hold id 1 asserted while sending byte-identical frames.  The
# 30 ms grace never arms because last_real keeps refreshing, so without this the
# press would last forever.  Stuck time is shortened so the test is quick.
out18, panel18 = make(stuck_secs=0.5)
panel18.feed(dec(report([(1, True, 6000, 6000)])), now())
check("frozen: press started", out18.down is True)
for _ in range(12):                       # 12 x 100 ms = 1.2 s, all identical
    advance(100)
    panel18.feed(dec(report([(1, True, 6000, 6000)])), now())
    panel18.tick(now())
check("frozen: released", out18.down is False)
check("frozen: counted once", panel18.stuck_presses == 1, panel18.stuck_presses)

# It must not re-press on the very next frame: the contact is still asserting
# itself, and re-pressing would turn one stuck contact into a press/release
# cycle every stuck interval.
advance(100)
panel18.feed(dec(report([(1, True, 6000, 6000)])), now())
panel18.tick(now())
check("frozen: not re-pressed while still asserting", out18.down is False)
check("frozen: still counted once", panel18.stuck_presses == 1,
      panel18.stuck_presses)

# Once the panel says the finger is up, a normal tap works again.
advance(100)
panel18.feed(dec(report([])), now())
advance(50)
panel18.feed(dec(report([(1, True, 7000, 7000)])), now())
check("frozen: recovers after the lift", out18.down is True)

# Motion has to reset the timer or a slow slider drag would be cut short.
out19, panel19 = make(stuck_secs=0.5)
panel19.feed(dec(report([(1, True, 6000, 6000)])), now())
for i in range(12):                       # same span, but moving every frame
    advance(100)
    panel19.feed(dec(report([(1, True, 6000 + 50 * (i + 1), 6000)])), now())
    panel19.tick(now())
check("moving: still down", out19.down is True)
check("moving: not counted as stuck", panel19.stuck_presses == 0,
      panel19.stuck_presses)

# One unit of jitter is motion, not a frozen contact.
out20, panel20 = make(stuck_secs=0.5)
panel20.feed(dec(report([(1, True, 6000, 6000)])), now())
for i in range(12):
    advance(100)
    panel20.feed(dec(report([(1, True, 6000 + (i % 2), 6000)])), now())
    panel20.tick(now())
check("jitter: still down", out20.down is True)

# It can be switched off for a page that wants indefinite holds.
out21, panel21 = make(stuck_secs=0.0)
panel21.feed(dec(report([(1, True, 6000, 6000)])), now())
for _ in range(12):
    advance(100)
    panel21.feed(dec(report([(1, True, 6000, 6000)])), now())
    panel21.tick(now())
check("disabled: never released", out21.down is True)
check("disabled: none counted", panel21.stuck_presses == 0)

print()
if failures:
    print("FAILURES: %s" % failures)
    sys.exit(1)
print("all driver scenarios passed")
