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

### Coverage: what the rule does not catch

`probes/simulate-ghost-press-filter.py --blips` lists every press that lasted
25 ms or less and never moved. A human tap on this panel is normally 50-150 ms
and moves a little; a phantom is 2-3 report frames and is perfectly still. Over
the five captures that set contains 7 presses:

| capture | dur | gap to prev | dX | dY | at | rule |
|---|---|---|---|---|---|---|
| session 2 | 11 | 168 | 1249 | 1931 | (6569,5353) | reported |
| session 2 | 10 | 273 | 329 | 222 | (6240,5575) | reported |
| session 2 | 12 | 53 | 46 | 2216 | (6286,7791) | **dropped** |
| session | 9 | 664 | 1549 | 1765 | (8994,8894) | reported |
| session | 18 | 43 | 14 | 2877 | (8980,6017) | **dropped** |
| mode 1 | 20 | 1931 | 6607 | 2341 | (9712,5087) | reported |
| ContactMax=1 | 10 | 359 | 3552 | 1536 | (4414,8009) | reported |

So the rule catches 2 of 7. The other 5 arrive 168-1931 ms after the previous
press, outside the 120 ms window, and reach the page as presses. If the
wrong-cell taps come from those, this filter does not address them.

They cannot be caught by duration alone. The shortest press that is *not* a blip
is 27 ms, so a 25 ms duration threshold has about 2 ms of margin — a very
different proposition from the position rule's factor-of-14 margin, and a fast
human tap would become a dead tap.

### The comparison point, and an unresolved ambiguity

The rule compares the new press against where the previous press *started*.
Using where it *ended* would catch two more presses in the captures, and both
follow a swipe (previous press 457 ms / moved 1326, and 431 ms / moved 233):

```
gap 74 ms  new press (8338,8451) dur 104  -> end drops, origin keeps
gap 53 ms  new press (4186,6412) dur  50  -> end drops, origin keeps
```

Those are jumps of 1458 and 3011 raw units in 53-74 ms, which is faster than a
hand and therefore probably a ghost. But the same shape is produced by an
ordinary "swipe, then tap just below" gesture, and the captures cannot separate
the two. Because a wrong suppression is a dead tap, the conservative base was
kept. `--bases` reproduces the comparison, and `tests/test-touch-driver.py`
pins the behaviour under the name "known limitation: tap right after a vertical
drag is dropped".

The observation that would settle it: **drag to scroll, then immediately tap
something in the same column.** If that tap never fails, the origin base is
fine and the two extra events were ghosts. If it does fail, the base — or the
whole shape of the rule — needs rethinking.

## 10. The panel's USB link sits behind a Thunderbolt dock, and it dropped once

At 16:30:34 on 2026-11-18 the kernel tore down the whole USB controller, not
just the device:

```
usb 3-2: USB disconnect, device number 2
usb 3-2.3: USB disconnect, device number 3
xhci_hcd 0000:3c:00.0: remove, state 4
usb usb4: USB disconnect, device number 1
xhci_hcd 0000:3c:00.0: USB bus 4 deregistered
usb usb3: USB disconnect, device number 1
xhci_hcd 0000:3c:00.0: USB bus 3 deregistered
pcieport 0000:07:00.0: Unable to change power state from D3hot to D0, device inaccessible
pci_bus 0000:08: busn_res: [bus 08] is released
pci_bus 0000:09: busn_res: [bus 09-3b] is released
pci_bus 0000:3c: busn_res: [bus 3c] is released
```

Ten seconds later it came back and re-enumerated:

```
16:30:44 usb 3-2.3: New USB device found, idVendor=27c6, idProduct=8191
16:30:44 hid-multitouch 0003:27C6:8191.0008: ... hidraw0
```

The controller is:

```
3c:00.0 USB controller [0c03]: Intel Corporation JHL6540 Thunderbolt 3 USB
Controller (C step) [Alpine Ridge 4C 2016] [8086:15d4] (rev 02)
```

So the path is panel → generic USB2 hub (`1a40:0101`) → Thunderbolt 3 dock's
xHCI → PCIe switch → host. The `D3hot → D0` failure on the bridge is a
power-management/link failure in the dock path, not a panel event: the panel was
never individually disconnected.

**This is a separate fault from §5 and §9.** It removes touch entirely for the
outage window, so any tap during it is lost — which is a candidate explanation
for "the tap registered but nothing happened until I pressed again", but not for
the ghosts or the latch, which appear in captures taken with no re-enumeration
at all. It has happened **once this boot** (one `USB bus 3 deregistered`, one
`D3hot` failure), so it is currently an observed one-off rather than a recurring
fault. If it recurs it deserves its own investigation — dock power management,
cable, or the host's TB controller.

### The harness was lying about it

The same event exposed a defect in `probes/touch-feed.py`. Its reader thread
opened `/dev/hidraw0` once and did not catch the error:

```
Exception in thread Thread-1 (reader):
OSError: [Errno 5] Input/output error
  File ".../probes/touch-feed.py", line 62, in reader
    data = os.read(fd, 64)
```

The HTTP thread kept running, so `/api/raw` and `/api/marks` returned frozen
16:26 data and the page looked like a screen nobody was touching. The reader now:

- finds the node by HID id in sysfs rather than assuming `/dev/hidraw0` (this
  event moved the panel's sysfs id from `.0001` to `.0008`; the device node
  number is not stable);
- reopens on `OSError` with a 1 s backoff instead of dying;
- and `/api/health` reports `link: {path, open, reconnects}`, so a dead feed is
  visible rather than silent.

Consequence for the record: **page-side measurements taken through `touch-feed`
between 16:30 and 17:06 on 2026-11-18 have no ground truth** — the feed was dead
for that window. The driver's own journal was unaffected, because the driver
already had reconnect handling.

### The mitigation, and why it is the right lever

The failing line is a *resume* from D3hot, and the bridge that logged it was
under runtime power management:

```
0000:06:00.0  control=auto     Alpine Ridge upstream port
0000:07:00.0  control=auto     the port that failed D3hot -> D0
0000:07:01.0  control=on
0000:07:02.0  control=on
0000:07:04.0  control=auto
0000:3c:00.0  control=auto     Alpine Ridge USB controller
```

So the lever is not to fix the resume but to never enter D3hot. The driver now
installs `deploy/90-thunderbolt-no-d3hot.rules`, which matches the two Alpine
Ridge device ids (`8086:15d3` bridge, `8086:15d4` USB controller) by
vendor/device rather than bus address — a re-enumeration renumbers the chain —
and sets `power/control=on` on every one of them. `install.sh` also applies it
to the already-present devices, because udev only fires on `add`.

All six now read `on`. The idle power cost is negligible on a kiosk that is
never suspended, and it is reversible by deleting the rule.

**This is evidence-based but unproven.** The fault has occurred once, so it
cannot be made to recur on demand; the honest test is to watch for a repeat.
If it recurs with runtime PM pinned, the cause is power delivery or controller
firmware, and the panel's USB would have to leave the Thunderbolt controller —
which needs a spare USB port the kiosk does not have, or a USB-C demux adapter.

Disabling ASPM (`pcie_aspm=off`) is the next lever if this one is not enough;
it costs a kernel command line change and a reboot, so it is not applied yet.

A note on the alternative that was considered and rejected: moving the panel's
touch to a plain USB-A port would remove this failure mode entirely, because
the display already bypasses the Thunderbolt PCIe switch (it is DP alt-mode on
`i915 DP-2`). It was rejected on the hardware, not the merits: the kiosk has no
free USB ports, and the panel appears to be single-cable.

## 11. The earlier evdev filter and this driver agree about identity

Before the HID rewrite the kiosk ran an evdev-level filter,
`/usr/local/bin/touch-filter.py`, which had been left on the machine with no
service pointing at it. It is now preserved as
`reference/touch-filter-evdev.py` with
`deploy/touch-filter-evdev.service` so it can be switched back to in one
command. It works on the `hid-multitouch` node and therefore cannot see contact
ids at all; instead it trusts by age:

- a contact born with nothing else live is trusted at once;
- a contact born while another is live must survive 200 ms (phantoms die within
  180 ms);
- contacts born in the same frame are treated as a phantom storm and only the
  **highest slot** is trusted;
- a contact frozen for 5 s is force-released and then ignored until it lifts;
- when no trusted contact remains, contacts still down are dropped as debris.

The "highest slot" rule looked like a direct contradiction of this driver's
"id 1 is the real finger" rule. It is not:

**id 1 is the last tipped contact in the frame in 94.7% of multi-contact frames
(868 of 917).** `hid-multitouch` assigns slots in report order, so the last
block becomes the highest slot. The two rules name the same contact from two
layers.

### What was ported, and what was not

Ported: **the frozen-contact release.** This driver had a real hole here. The
panel is change-driven, but the firmware can hold id 1 asserted while emitting
byte-identical frames. Because the contact keeps being reported, `last_real`
keeps refreshing and the 30 ms absence grace never arms, so the press never
ends. A press that reports without moving for 5 s is now released, and that
contact id is ignored until it lifts — without the second half, the still-
asserting contact is re-pressed on the next frame and you get a press/release
cycle every stuck interval instead of one stuck press. Motion resets the timer,
so a slow slider drag is unaffected. `TOUCH_STUCK_MS=0` disables it.

Not ported, because this driver already has the equivalent:

| earlier rule | why it is not needed here |
|---|---|
| highest slot in a cohort | same contact as id 1, 94.7% measured |
| 200 ms trust for a second contact | ids 2-10 are never trusted at all |
| drop debris when the gesture ends | there is no debris to promote; nothing but id 1 is tracked |

Not fixed by either filter: the latch. It asserts id 1 with motion, so a
stillness timer does not fire, and it never produces a second press for the
ghost guard to compare against.

The ghost guard's coverage limit from §9 is unchanged: it catches 2 of the 7
ultra-short phantom presses in the captures. If wrong-cell taps persist, the
next thing to widen is `TOUCH_GHOST_GAP_MS`, and the two surviving hypotheses
are recorded under "The comparison point, and an unresolved ambiguity".
