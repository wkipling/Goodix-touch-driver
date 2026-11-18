# Addendum to the Goodix driver notes

Written 2026-11-18 while rewriting the driver. The main document is
[`GOODIX-TOUCH-DRIVER-NOTES.md`](GOODIX-TOUCH-DRIVER-NOTES.md); this file only
records things measured after it was written, and one correction.

## 1. Correction: uinput *can* take `fuzz`, but Xorg takes it away

The notes say (§6.3, §7) that a uinput device will not take an `EVIOCSABS`
fuzz setting, and that a read-back on the same handle lies. That is correct
about `EVIOCSABS`, and correct about trusting the handle.

It is not the only interface. `UI_DEV_SETUP` plus `UI_ABS_SETUP`, which
python-evdev uses when `AbsInfo` values are supplied to `UInput()` at
creation, does set the fuzz. Measured:

```
$ sudo python3 probes/probe-uinput-fuzz.py
created /dev/input/event18
ABS_X                  value=0 min=0 max=16383 fuzz=80 ...
RESULT: fuzz survives
```

But it does not *stay*. `probes/probe-uinput-persist.py` shows the sequence:

```
0.00s  fuzz=[80, 80, 80, 80]
0.25s  fuzz=[0, 0, 0, 0]
```

`probes/probe-who-zeroes.py` names the writer: Xorg opens the new event node
and the value drops at the same moment.

```
0.06s fuzz=80 openers=[('101440', 'python3 .../probe-who-zeroes.py')]
0.13s fuzz=0  openers=[..., ('91330', '/usr/lib/xorg/Xorg :0 ...'), ...]
```

So the practical conclusion in the notes stands: **the kernel fuzz on the
virtual device cannot be relied on while X is running.** The reason is Xorg
rewriting absinfo at open, not uinput refusing it.

Consequence for the driver: the 80-unit deadband is applied in-process,
against the last *emitted* value. The absinfo fuzz is still set at creation as
a best-effort hint and logged again after Xorg has opened the node.

If someone wants kernel-side fuzz instead, it would have to be re-applied
after Xorg's open window (and would be at risk on VT switches or device
re-registration). The in-process deadband has no such race.

## 2. The deployed shim was still the 5-block decoder

At the time of this work `/usr/local/bin/touch-filter-hid.py` decoded five
finger blocks. The ten-block fix existed in
`/home/kiosk/touch-tests/reference-touch-filter-hid.py` but had not been
deployed. The rewritten driver decodes ten, and treats a short payload
tolerantly rather than rejecting it.

## 3. Device discovery

The old shim hard-coded `/dev/hidraw0` and the by-id path for `event7`.
Both still resolve correctly today, but the rewrite selects the hidraw node
whose sysfs target is `0003:27C6:8191.0001`, and selects the raw evdev node by
vendor/product plus `ABS_MT_SLOT` (skipping virtual devices). This is the same
capability rule `touch-fuzz.py` uses, and it survives a changed event number.

## 4. Contact Count: measured, but not an identity signal

`Contact Count` (payload offset 52) was captured with the correct ten-block
layout. Every on-wire report was 54 bytes. The count is populated (values 1–7
across the two sessions, never 0 while contacts are present), but it equals
the number of tipped blocks in only 74–84% of reports and counts phantoms
too. It cannot tell which contact is the real finger. Id 1 remains the only
identity signal — and, as §5 shows, it is not always the real finger either.

## 5. The firmware latches contact id 1

A tap can become a 3–4 s press even though the page's release logic is sound.
The firmware keeps id 1 asserted and streams a synthetic X oscillation while
Y stays fixed; because id 1 never clears, the driver's release rule never
arms. In a second form, id 1 reappears as a ghost after a real lift and can
produce two presses 10 ms apart. This is a firmware defect that no driver can
infer around. Full evidence, the device-level mode control, and the update
availability are in [`FIRMWARE-LATCH.md`](FIRMWARE-LATCH.md).

## 6. Correction: feature 0x21 is Input Mode, not brightness

The notes describe feature `0x21` as brightness-like. On the Digitizer page,
usages `0x52`/`0x53` are **Input Mode** and **Device Input Mode**. `usbmon` at
bind shows `hid-multitouch` sending `SET_REPORT 0x21 = 21 02 00` (InputMode=2,
DeviceInputMode=0). See [`FIRMWARE-LATCH.md`](FIRMWARE-LATCH.md).

## 7. No public update for this controller

The official Goodix HID updater (v1.8.0) does not list PID `8191` and uses a
different report protocol. Mainline Linux has no `27c6:8191` quirk, the
Microsoft Update Catalog has no entry for the hardware ID, and the panel EDID
is `EMO` / `Monitor` / `demoset-1`. A firmware image/tool would have to come
from the panel vendor.

## 8. The phantom geometry, measured

A second pass on the two sessions decoded the panel's own report descriptor and
measured *where* the spurious contacts sit, which the earlier pass did not do.
Two results:

- **Contact Count is monotone but wrong.** It is never less than the number of
tipped blocks (0 violations in 1587 reports), equals it in 84.5% / 74.2% of
reports, exceeds it by one in 148 / 121 reports, and further in 14 / 19. So the
count belongs to the tracker, not to the frame, and it cannot be used to
subtract a stuck contact.
- **Phantoms share the real finger's X.** Across 608 multi-contact reports the
median X spread between contacts is 15–29 raw units (~1.5–3 CSS px) while the
median Y spread is 2374–4637 units, a quarter of the screen. Y also pins to its
logical limits (`0`/`1` and `9599` = `0x257F`) far more than chance allows,
while X never pins.

That points at slots which are never invalidated on release, not at random
corruption, and it is why the wire format's Scan Time field (constant `0x0101`
in every report) offers no help. Full detail, the latch's micro-structure, the
bus checks and the revised lever list are in
[`FIRMWARE-LATCH.md`](FIRMWARE-LATCH.md).

## 9. The ghost-press guard, and why its thresholds are where they are

The addendum above says phantoms share the real finger's X. A consequence was
visible on the kiosk but not yet encoded: a tap sometimes registers in the
wrong cell, one row above or below the cell that was touched, with no ripple.
That is the phantom geometry reaching userspace as a second press — the real
tap lands, is released, and a moment later an id-1 contact appears at the same
X and an unrelated Y.

`src/goodix-touch-driver.py` now drops a press that begins **within 120 ms of
the last report of the previous press, within 120 raw units in X, and 400 or
more raw units away in Y**. The gap is measured from when the finger was last
seen rather than from the emitted release, which the 30 ms grace period would
shift. It is implemented as a `Panel` guard (`GHOST_GUARD`), not as a change in
the release rule, and it counts and logs what it drops.

### The margin, measured

`probes/simulate-ghost-press-filter.py` replays captures through the driver's
id-1 state machine. Over the five captures (131 presses) the rule drops
exactly two presses, both of them the impossible pairs already documented in
§5:

| dropped | gap to previous press end | dX | dY |
|---|---|---|---|
| `(6286,7791)` | 53 ms | 46 | 2216 |
| `(8980,6017)` | 43 ms | 14 | 2877 |

`--margin` prints every press pair in the captures whose X agrees within 200
raw units, so the thresholds can be judged against the nearest legitimate
press rather than against the ghosts alone. The result is that the two
populations do not overlap at all:

```
gap_ms    dX     dY dur_ms  at             verdict
    53    46   2216     12  (6286,7791)    DROPPED
   787    42     73     82  (5783,6906)    kept
   788   110     69     82  (5745,6921)    kept
   ...
```

The highest gap among ghost-shaped pairs is 53 ms; the lowest gap among
legitimate same-column pairs is 787 ms. Any threshold from about 60 to 780 ms
produces identical output, so 120 ms is not a tuned value — it sits in an
empty band a factor of 14 wide. Similarly, no legitimate same-column pair in
the captures occurs within 120 ms at all, so the `dY >= 400` condition has no
close competitor either; it is the *conjunction* (same column, different row,
faster than a human double tap) that identifies the ghost, and each condition
alone would be unsafe.

### What it does not do

- It is a filter over a firmware defect, not a repair. The phantom remains in
the report stream; the driver only declines to turn that particular one into a
press.
- It does nothing for the latch (§5). A latched id 1 never produces a second
press to compare against, so there is nothing for the rule to fire on.
- The sample is bounded: 131 presses, 5 captures. The claim is that no
legitimate press in *this* sample is dropped and that the class of press it
drops is not physically achievable. A deliberately pathological input — two taps
in the same column, to different rows, less than 120 ms apart — would be
dropped. That is faster than 8 taps per second, sustained.
- The horizontal tolerance is 120 raw units (~12 CSS px). A ghost that drifts
further than that in X would be missed; none of the captured ones do (dX 14 and
46). If a future capture shows a wider one, widen `TOUCH_GHOST_DX` and re-run
`--margin` rather than guessing.

Knobs: `TOUCH_GHOST_GUARD=0` disables the guard, `TOUCH_GHOST_GAP_MS`,
`TOUCH_GHOST_DX` and `TOUCH_GHOST_DY` override the thresholds. The startup log
prints the active policy.
