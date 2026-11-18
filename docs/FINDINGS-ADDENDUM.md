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
