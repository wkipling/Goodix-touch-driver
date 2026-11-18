# Goodix 27c6:8191 touch panel — notes for rewriting the driver

Written 2026-11-18 on the kiosk itself (`kiosk.local`, Debian 13 trixie,
kernel 6.12.116+deb13-amd64). Everything here was measured on this machine;
where something is inferred rather than measured it says so explicitly.

**Purpose of this document.** The panel's firmware is defective, the stock
`hid-multitouch` driver cannot compensate for it, and there is a Python shim
in the path doing so badly. The goal is to replace the shim with a proper
input driver. This is the evidence and the requirements it would have to meet.

---

## 1. The symptom to fix

A single tap on the Home Assistant dashboard sometimes behaves as a long
press. Concretely: tapping a card that should toggle a light instead grows
Home Assistant's ripple into a large blue translucent circle, and/or opens a
more-info dialog instead of toggling.

Two independent things cause that appearance, and they must not be confused:

1. **A press that never ends.** If the touch is never released, the page's
   hold logic wins regardless of how briefly the finger was down.
2. **A press that lasts too long.** Home Assistant's ripple element is a
   100px disc that animates `scale(0)` → `scale(1)` over 180ms. Measured on
   this page: at 100ms into a press it is at scale 0.29, at 250ms it is at
   full size. So any press of roughly 180ms or more *shows* the large circle.

Measured on this page, a press produces a real `tap` up to about 170ms and
produces nothing at all at 190ms and beyond — the tap is dropped, not converted
to a hold. So the usable window for a press is roughly **≤170ms**, and the
ripple starts to look wrong well before that.

Chromium's touch slop is **8 CSS px**. A press that moves further than that is
treated as a scroll and cancelled. This matters because the panel's reported
position drifts while a finger is held still (§4.2).

Coordinate spaces on this kiosk:

- panel native: 3840 x 2400
- device scale factor 2.5 → logical viewport 1536 x 960 CSS px
- touch axes report 0..16383 (X) and 0..9599 (Y)
- so `css_x = raw_x * 1536 / 16384` and `css_y = raw_y * 960 / 9600`
  (i.e. ×0.09375 and ×0.1)

---

## 2. Hardware topology

```
Bus 003 Device 003: ID 27c6:8191 Shenzhen Goodix Technology Co.,Ltd. TOUCH
bcdDevice 1.00, USB HID v1.10
```

Three HID interfaces, all named "TOUCH TOUCH":

| HID device | interface | endpoints | driver bound | notes |
|---|---|---|---|---|
| `0003:27C6:8191.0001` | 0 | 1 | **hid-multitouch** | multitouch + stylus + keyboard in one descriptor |
| `0003:27C6:8191.0002` | 1 | 2 | hid-generic | hiddev only |
| `0003:27C6:8191.0003` | 2 | 1 | hid-generic | mouse emulation |

hidraw mapping: `hidraw0 → .0001` (the one that matters), `hidraw1 → .0002`,
`hidraw2 → .0003`. `hidraw3` is an unrelated BLE remote.

Input nodes created from interface 0:

- `event7` — "TOUCH TOUCH", the multitouch device (has `ABS_MT_SLOT`)
- `event6` — "TOUCH TOUCH Stylus" (bound to the `wacom` driver in X; never
  emits anything during finger touch — verified, zero events across captures)
- `event8` — "TOUCH TOUCH Keyboard"
- `event9` — "TOUCH TOUCH", a mouse-emulation node for the same interface

Note `event7` and `event9` share the same name. Anything selecting the device
by name will pick the wrong one. Select by capability instead: the touchscreen
is the one advertising `ABS_MT_SLOT` (0x2f) and `INPUT_PROP_DIRECT`.

---

## 3. Report descriptors (interface 0)

Dump with:

```sh
sudo mount -t debugfs none /sys/kernel/debug   # if not already mounted
sudo cat /sys/kernel/debug/hid/0003:27C6:8191.0001/rdesc
```

Four reports on interface 0:

| Report ID | Type | Contents |
|---|---|---|
| `0x06` | Input | Digitizer / Pen — TipSwitch, BarrelSwitch, Eraser, Invert, InRange, X, Y, TipPressure, XTilt/YTilt |
| `0x0D` | Input | Digitizer / TouchScreen — **the multitouch data** |
| `0x09` | Input | Keyboard — standard 6KRO boot-style keyboard |
| `0x21` | Feature | Keyboard/display brightness-ish collection (usages 0x52/0x53) |
| `0x0A` | Feature | `Contact Count Maximum` = **10** |

### 3.1 Report 0x0D layout — exact

**Ten** finger blocks, each 5 payload bytes, then Scan Time, then Contact
Count. Verified by counting the descriptor's `09 22 a1 02 09 42` blocks: 10
occurrences, each 63 descriptor bytes, sequential.

Per finger block, bit-level:

```
bit 0        TipSwitch             (1 = finger present)
bits 1..3    padding
bits 4..7    Contact Identifier    (4-bit, 0..15)
bytes 1..2   X, 16-bit little-endian, logical 0..16383
bytes 3..4   Y, 16-bit little-endian, logical 0..9599
```

Payload offsets:

```
block n (n = 0..9)  at  5*n      .. 5*n+4
Scan Time           at  50..51   16-bit LE
Contact Count       at  52       8-bit, logical max 127
Total payload 53 bytes; +1 report ID byte = 54 bytes on the wire
```

Python decode:

```python
REPORT_ID = 0x0D
FINGERS   = 10

def decode(payload):          # payload excludes the report-ID byte
    out = []
    for n in range(FINGERS):
        b0 = payload[5*n]
        if not (b0 & 0x01):
            continue
        cid = (b0 >> 4) & 0x0F
        x = payload[5*n+1] | (payload[5*n+2] << 8)
        y = payload[5*n+3] | (payload[5*n+4] << 8)
        out.append((cid, x, y))
    scan_time     = payload[50] | (payload[51] << 8)
    contact_count = payload[52]
    return out, scan_time, contact_count
```

### 3.2 Descriptor inconsistencies worth knowing

- `Contact Count Maximum` (feature 0x0A) says **10**, matching the 10 blocks.
  `Contact Count` in report 0x0D allows up to 127, which is meaningless given
  only 10 blocks exist. Any count above 10 is unsatisfiable.
- The device declares `fuzz = 0` on both X and Y, so the input core passes on
  every single-unit wobble (see §4.2).

---

## 4. Measured firmware defects

Captured by reading `/dev/hidraw0` directly with `decode()` above, bypassing
the kernel. Raw HID, not evdev — this is what the panel's firmware actually
says.

### 4.1 One tap arrives as a burst of 2–10 contacts

A single finger tap is usually reported as several tipped blocks in the same
report. Measured over one capture of 452 reports: 201 reports with 1 tip, 116
with 2, 84 with 3, 19 with 4, 4 with 5. The extra "contacts" are spurious.

**The real finger is always HID contact id 1.** Measured across 14 separate
gestures:

- 198 of 198 single-contact ("solo") reports carried id 1
- in every multi-contact report, id 1 was present
- the only solo reports that were *not* id 1 were spurious (see 4.3)

The spurious contacts carry ids 2..6 and hold **the real finger's X with a
garbage Y** — or the reverse. One measured example at t=97.385s, the real
finger at (5843, 6815):

```
id 2: (5846, 3583)   <- phantom, X matches the real finger, Y is invented
id 1: (5843, 6815)   <- real finger
```

and the phantom's Y then swept steadily upward across ~80ms:

```
3583 -> 5277 -> 6409 -> 7580 -> 8813 -> 9237
```

That smooth ramp is why this looks like a deliberate second coordinate rather
than random noise. **What the firmware is actually doing here is not known.**
What *is* established is that only id 1 corresponds to a finger the user
placed.

### 4.2 Position drifts while the finger is held still

During a stationary press the reported position wanders. Measured travel
within a single press, in CSS px: commonly 5–22, occasionally far more. Two
real samples:

```
a tap that worked:   x 547->547,  y 685->685   (0 px travel)
a tap that failed:   x 543->542,  y 673->695   (22 px travel)
```

22 CSS px is well past Chromium's 8 px touch slop, so the tap is turned into a
scroll and cancelled. This is the second half of the "only sometimes" symptom.

Because the device declares `fuzz = 0`, the kernel was passing all of this on.
Kernel-side `fuzz` suppression is the correct fix for this half, and it was
verified to work on this hardware (§6.3) — but it **cannot** be applied to a
`uinput` device (§8), which is one of the reasons the current shim is wrong.

### 4.3 Spurious contacts also arrive alone

Separate from the bursts: some reports contain exactly one tipped block that is
*not* id 1. Measured: 3 of 201 solo reports were id 5, at coordinates nowhere
near where the user was touching (e.g. `(0, 684)` in CSS terms, the far left
edge, while the user was tapping at x≈547).

This is the nastiest case for any filter, because "a contact born alone is a
real touch" is otherwise a sound rule. It is not sound on this panel.

### 4.4 What is NOT established

- **I do not know whether `Contact Count` is populated correctly.** An earlier
  note in this project claimed it was always 0. That was wrong, and it was my
  bug: the decoder was reading 5 blocks instead of 10, so the byte it took as
  "Contact Count" was actually part of block 6. The correct offsets are 50
  (Scan Time) and 52 (Contact Count). **This needs re-measuring** with the
  correct layout — it is the single most useful missing fact, because if the
  firmware does report a correct Contact Count, a driver can simply believe it
  and ignore every block beyond that count.
- The exact mechanism behind the spurious blocks is not characterised.
- Whether the `0x06` pen report ever interleaves with `0x0D` during finger
  touch — a capture showed no pen events, but it was a short one.

---

## 5. What `hid-multitouch` does, and why it is not enough

`hid-multitouch` is bound to `0003:27C6:8191.0001` (default class; no quirks
line appears in `dmesg` for this device). It:

- parses the finger blocks itself,
- assigns its own `ABS_MT_SLOT` values,
- synthesises its own `ABS_MT_TRACKING_ID` values,
- and **discards the firmware's `Contact Identifier`**.

That last point is the crux. By the time the events reach evdev, there is no
way to tell which contact was id 1 — the only field that reliably identifies
the real finger has been thrown away. Every userspace attempt to filter at the
evdev layer is therefore guesswork, and all of them failed (§7).

A correctly written driver does not have this problem: it has the contact id
in hand and can decide before assigning a slot.

### 5.1 A note on the dmesg sequence

At boot, `hid-generic` binds first and creates input8/9/10, then
`hid-multitouch` takes over and creates input12/13/14 for the same interface.
This is ordinary driver rebinding, not a second physical device.

---

## 6. Current state of the machine

```sh
# services
touch-fuzz.service        # sets kernel fuzz on the RAW panel's axes
touch-filter.service      # the Python shim (see §7)
kiosk.service             # startx + openbox + Chromium
kiosk-touch-fix.service   # CDP: forces touch-action:none on sliders
kiosk-noscroll.service    # CDP: prevents 1-2px page micro-scroll
kiosk-hide-header.service # CDP: hides the HA header
```

Files:

```
/usr/local/bin/touch-fuzz.py             # fuzz setter for the raw panel
/usr/local/bin/touch-filter-hid.py       # THE SHIM (reads hidraw0)
/usr/local/bin/touch-filter.py           # older evdev-level shim (superseded)
/usr/local/bin/touch-filter.py.bak-evdev # backup: last evdev version
/usr/local/bin/touch-filter.py.bak-frames# backup: original frame-counting version
/usr/local/bin/kiosk-check               # self-check, prints what each line expects
```

### 6.1 Chromium launch flags that matter

```
--touch-events=enabled
--disable-pinch                  # so a lost second finger costs nothing
--force-device-scale-factor=2.5
--remote-debugging-port=9222     # used by the CDP helper scripts
```

### 6.2 The shim, and what it gets wrong

`touch-filter-hid.py` currently:

1. grabs the raw evdev node (so X cannot open it) but never reads it,
2. reads report `0x0D` from `/dev/hidraw0`,
3. **decodes only the first 5 of the 10 finger blocks** — a real bug; contacts
   in blocks 6–10 are invisible to it,
4. trusts contact id 1 as the real finger,
5. emits a single-contact stream onto a fresh `uinput` touchscreen,
6. applies an 80-unit deadband to motion by hand, because `uinput` refuses
   `EVIOCSABS` (§8),
7. releases the press when id 1 has been absent for 30ms, or immediately on an
   empty report.

### 6.3 Kernel fuzz on the raw panel

`touch-fuzz.service` sets `fuzz=80` on `ABS_X`, `ABS_Y`, `ABS_MT_POSITION_X`
and `ABS_MT_POSITION_Y` of `event7`. **This is verified working**: on a test
device, `fuzz=150` suppressed movements of 40 and 80 units while passing a 400
unit movement. 80 units ≈ 7.5 CSS px in X, 8 in Y.

It does not help the shim, because the shim re-emits onto its own device and
X reads *that* device, not `event7`. If the shim is removed and X is put back
on the real panel, this setting is what removes the drift half of the problem.

Two ways to apply it, both tried:

- **udev `EVDEV_ABS_*` — does not work here.** Debian's
  `/usr/lib/udev/rules.d/60-evdev.rules` only reads hwdb; it does not apply
  `EVDEV_ABS_00` etc. Setting the property and re-triggering has no effect.
  The property format is `value:min:max:fuzz`, not `min:max:fuzz`.
- **Direct `EVIOCSABS` ioctl — works.** That is what `touch-fuzz.py` does.

---

## 7. Things already tried that did not work

Recorded so they are not repeated. All of these were tried at the **evdev**
layer, which is why they all failed: the contact id is gone by then.

| Attempt | Why it failed |
|---|---|
| Trust a contact "born alone", defer others by N frames | Device is change-driven: it sends nothing at all while idle (0 reports in 15s). Frame counts don't advance during a still press, so the "150ms" window was really "until the finger moves again". |
| Trust a contact born alone, defer others by 200ms | Real second contacts were starved and dropped — a real tap landing during a burst died unsent. |
| Verdict stamped at birth | Cannot change later, so a contact promoted by age was never reconsidered when a real finger arrived. |
| Highest `ABS_MT_SLOT` in a burst is the real finger | Partly right for bursts, wrong for solo spurious contacts, and slot order is not guaranteed by anything. |
| 100ms release grace | **Caused the reported symptom.** It pushed every tap from ~90ms to ~200ms, past the 180ms ripple threshold and past the ~190ms tap window. |
| Movement commit threshold in the evdev filter (suppress motion until 28 CSS px of travel) | Suppressed legitimate small drags. Also unnecessary: kernel fuzz does this correctly. |
| `EVDEV_ABS_*` udev properties | Not applied by this distro's udev (see 6.3). |
| `EVIOCSABS` on the `uinput` device | Silently a no-op. The ioctl returns success and a read-back on the same handle reports the new value, but a **fresh open of the event node still reports `fuzz=0`**. Trusting that read-back produced a false "noise floor applied" log line. |

---

## 8. Requirements for a proper driver

If writing a kernel driver (or a userspace one that owns the device properly):

1. **Parse report `0x0D` with all 10 blocks** at the offsets in §3.1.
2. **Determine the real contact set.** Today the reliable signal is contact id
   == 1. A better signal may be `Contact Count` (§4.4) — measure it first.
   Whatever the rule, it must handle: bursts of 2–10 contacts, and single
   spurious contacts with ids other than 1.
3. **Never let a press outlive the finger.** Release must be driven by the
   real contact disappearing, not by "no contacts at all" — after a real
   finger lifts, spurious contacts typically remain.
4. **Keep press duration honest.** Do not add latency that widens a press.
   Budget: a tap must land in the page at ≤170ms; achievable is ~90–130ms.
   Note the device is change-driven, so a release may have to be timed by a
   short tick rather than by waiting for another report.
5. **Set a noise floor on the axes.** `fuzz` of about 80 raw units suppresses
   the stationary drift (§4.2, §6.3). Do it in the driver (set `absinfo`
   before registering the input device) rather than via udev or by
   hand-filtering motion.
6. **Suppress motion until it is real, but keep small drags.** With fuzz set,
   genuine short slow drags still work; a commit threshold is not needed.
7. **Preserve `INPUT_PROP_DIRECT`** so libinput treats it as a touchscreen and
   not a touchpad.
8. **Keep it single-touch.** Chromium runs `--disable-pinch`, so the second
   finger is not needed. Do not spend complexity on multi-touch fidelity;
   resolving id 1 correctly matters far more.
9. **Log the decision, briefly.** Which contacts were in the report, which was
   trusted, and why. Debug-level, off by default — but present, because this
   panel will keep surprising people.

### 8.1 Suggested shape

A small standalone HID driver that binds `0003:27C6:8191.0001`:

- read report `0x0D` in `raw_event`, decode 10 blocks, pick the real contact
  by id (or by Contact Count, once measured),
- ignore the pen report `0x06` and let the existing stylus/wacom path have it,
- register one input device with `EV_ABS` (MT slot/tracking id/X/Y plus
  `ABS_X/Y`) and `EV_KEY` (`BTN_TOUCH`), `INPUT_PROP_DIRECT`,
- set `fuzz` in the `absinfo` at registration,
- emit exactly one contact, and always release it.

Alternative, much less work: keep the panel on `hid-multitouch`, drop the
shim entirely, and rely on `touch-fuzz` for the drift half. That fixes
"taps cancelled by drift" but leaves the spurious-contact half unaddressed,
so it is a mitigation, not a fix. Worth benchmarking as a baseline before
writing a driver.

---

## 9. How to reproduce and verify

### 9.1 Read the raw reports

```sh
# everything the firmware says, decoded
sudo python3 - <<'PY'
import os, select, time
REPORT_ID = 0x0D
def decode(p):
    for n in range(10):
        b0 = p[5*n]
        if b0 & 0x01:
            print("  id=%2d x=%5d y=%5d" % ((b0>>4)&0x0F,
                  p[5*n+1] | (p[5*n+2]<<8), p[5*n+3] | (p[5*n+4]<<8)))
    print("  scan=%d count=%d" % (p[50] | (p[51]<<8), p[52]))
fd = os.open("/dev/hidraw0", os.O_RDONLY)
end = time.time() + 30
while time.time() < end:
    r,_,_ = select.select([fd],[],[],0.5)
    if not r: continue
    d = os.read(fd, 64)
    if d and d[0] == REPORT_ID and len(d) >= 54:
        print("report len", len(d)); decode(d[1:])
PY
```

The device is change-driven: **you will see nothing while it is idle.** It
only reports when the touch changes.

### 9.2 Check the drift is suppressed

Read `absinfo` on the device X actually uses:

```sh
sudo python3 -c "
import os, fcntl, struct
from evdev import ecodes as e
def ioc(d,nr,s=24): return (d<<30)|(s<<16)|(0x45<<8)|nr
fd=os.open('/dev/input/event7', os.O_RDONLY)
for c in (e.ABS_X,e.ABS_Y,e.ABS_MT_POSITION_X,e.ABS_MT_POSITION_Y):
    b=bytearray(24); fcntl.ioctl(fd, ioc(2,0x40+c), b)
    print(e.ABS.get(c), struct.unpack('iiiiii', bytes(b))[3])"
```

Expect `80` on all four. **Always read this through a fresh open of the event
node**, never through the uinput handle — see §7.

### 9.3 What the page decided

The browser is the thing being annoyed, so measure there. Helper scripts are in
`/home/kiosk/`. `Input.dispatchTouchEvent` over the DevTools port produces
trusted input, which confirmed the page's thresholds independently of the
panel:

```sh
# synthetic press of N ms at CSS coords, then read what HA dispatched
python3 /home/kiosk/cdp-tap.py 426 330 90      # 90ms  -> HASS-ACTION action=tap
python3 /home/kiosk/cdp-tap.py 426 330 200     # 200ms -> no action at all
```

`cdp-record.py arm` / `cdp-record.py read` installs and reads a recorder that
logs `pointerdown`/`pointerup` with hold duration plus the `hass-action` event,
which gives the authoritative verdict. Useful thresholds measured this way:
tap works to ~170ms, none at ~190ms; ripple reaches full size at ~250ms of
press.

### 9.4 Tests that exist

On the kiosk, in `/home/kiosk/touch-tests/`:

```
test-touch-hid.py      decode + trust rule (15 checks)
test-touch-release.py  press must end, and must stay short (16 checks)
test-touch-cohort.py   burst handling
test-touch-storm.py    leftover-contact and stuck-contact cases
test-touch-drift.py    drift scenarios, from measured paths
reference-touch-filter-hid.py   the current shim, as a baseline to compare against
```

They take a path to the implementation as argv[1], stub `UInput`, and drive a
fake clock, so they run without touching the panel:

```sh
cd /home/kiosk/touch-tests
python3 test-touch-release.py ./reference-touch-filter-hid.py
python3 test-touch-hid.py     ./reference-touch-filter-hid.py
```

Run them against any rewrite in place of the shim.

**The lesson from this investigation, encoded in these tests:** every earlier
round verified that the *right contact* was forwarded, and none verified that
the press *ended* or that it stayed *short*. All three matter.

---

## 10. Rollback

```sh
# stop the shim, put X back on the real panel, use kernel fuzz only
sudo systemctl stop touch-filter.service
sudo systemctl disable touch-filter.service
sudo systemctl restart kiosk.service      # X re-enumerates devices
/usr/local/bin/kiosk-check                # confirm what is in play
```

Spurious contacts return; the drift half stays fixed by `touch-fuzz.service`
provided that service still runs and X is using `event7`.

To go back to the previous evdev shim:

```sh
sudo install -m755 /usr/local/bin/touch-filter.py.bak-evdev \
  /usr/local/bin/touch-filter.py
# then point touch-filter.service's ExecStart back at it
sudo systemctl daemon-reload && sudo systemctl restart touch-filter.service
```

---

## 11. Loose ends

- [ ] Re-measure **Contact Count** (payload offset 52) with the correct
      10-block layout. This may make the whole id-1 heuristic unnecessary.
- [ ] Re-capture and confirm contact ids with all 10 blocks decoded — the
      earlier id analysis only saw blocks 1–5, so ids in blocks 6–10 have
      never been observed.
- [ ] Confirm whether report `0x06` (pen) interleaves with `0x0D` during
      finger touch.
- [ ] Decide: proper HID driver, or keep `hid-multitouch` + fuzz + accept the
      spurious contacts.
- [ ] If a driver is written: check whether the vendor's Windows tool can
      apply a firmware update. The `Contact Count Max = 10` versus 5-in-my-
      decoder confusion, and the spurious blocks, both smell like firmware
      bugs that a driver can only paper over.
- [ ] Unrelated but noticed: the kiosk has no BIOS battery, so the clock boots
      stale; `kiosk.service` waits on `time-sync.target` for that reason.
