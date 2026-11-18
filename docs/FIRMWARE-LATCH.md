# The firmware latch and the phantom contacts

Root cause of the residual hold, and what the controller does instead of
releasing. First written 2026-11-18 after the driver rewrite; revised the same
day after reading the panel's own report descriptors off the device and
measuring the geometry of the spurious contacts.

The revision moves the diagnosis from "a contact that will not release" to
**slots that are never invalidated**. Details and the strength of each claim
are separated below.

## Symptom

A tap occasionally becomes a multi-second press and Home Assistant's ripple
grows. The driver is delivering a held press, not failing to release one.

## Summary of what is established

| Claim | Evidence | Confidence |
|---|---|---|
| The wire format is exactly what the notes said: 54 bytes, 10 five-byte finger blocks, Scan Time, Contact Count | The panel's own 946-byte report descriptor, read from `report_descriptor` in sysfs | observed |
| Scan Time never advances; it is a constant `0x0101` | All 1587 captured reports | observed |
| Contact Count is never *less* than the number of tipped blocks | 1587 reports, 0 violations | observed |
| Contact Count exceeds the tipped count, up to +1 usually and further occasionally | 148/1044 and 121/543 reports at +1; 14 and 19 beyond that | observed |
| Spurious contacts carry the **real finger's X** and an invented Y | 608 multi-contact reports | observed |
| The raw-to-screen mapping is exact: X `10.67·css`, Y `9.99·css`, residual 3 raw units | 13 calibration taps at five known targets | observed |
| The latch is a frozen slot followed by a smooth oscillation | Two 3–4 s spans | observed |
| The USB link and power are clean | 46 h of dmesg, no errors or resets | observed |
| Phantom Y values are fresh, not copies: 66–75% unique, 0–2% near the real Y | 633 phantoms over three captures | observed |
| The controller fabricates slots: live X inherited, Y arbitrary | Inferring from the above | **inference** |

## 1. The wire format, from the device's own descriptor

Interface 0 carries a 946-byte report descriptor. Report `0x0D` is a
TouchScreen application containing **ten** Finger collections. Each block is
five payload bytes:

| Payload bytes (after the report ID) | Field | Usage | Size |
|---|---|---|---|
| `5n`, bits 0 | Tip Switch | Digitizer `0x42` | 1 bit |
| `5n`, bits 1–3 | padding | constant | 3 bits |
| `5n`, bits 4–7 | Contact Identifier | Digitizer `0x51` | 4 bits, 0–15 |
| `5n+1 .. 5n+2` | X | Generic Desktop `0x30` | 16 bits, 0–16383 |
| `5n+3 .. 5n+4` | Y | Generic Desktop `0x31` | 16 bits, 0–9599 |
| `50 .. 51` | Scan Time | Digitizer `0x56` | 16 bits, 0–65535 |
| `52` | Contact Count | Digitizer `0x54` | 8 bits, 0–127 |

Ten blocks (50 bytes) + 2 + 1 = 53 payload bytes, 54 on the wire, which is
exactly what the panel sends. Feature reports are `0x21` (Input Mode `0x52`,
Device Index `0x53`, both 8-bit, 0–10) and `0x0A` (Contact Count Maximum
`0x55`, 8-bit, value 10).

The usage numbering is the one the device actually uses — the Linux
`HID_DG_*` numbers, where `0x52`–`0x56` are InputMode, DeviceIndex,
ContactCount, ContactMax, ScanTime. The older HID usage tables assign those
codes differently, which is where the "brightness" reading in the original
notes came from. Two independent checks fix it: feature `0x0A` with usage
`0x55` and logical maximum 10 is a contact maximum, not a switch; and feature
`0x21` written as `02 00` is InputMode=2 (touchscreen), DeviceIndex=0.

**Scan Time is not a counter.** It was `0x0101` in all 1587 reports across both
sessions. It carries no liveness information and cannot be used to detect a
stalled stream.

The three interfaces, for reference:

| Interface | Driver | Endpoints | Descriptor |
|---|---|---|---|
| 0 | `hid-multitouch` | `0x81` IN | 946 bytes: pen `0x06`, touchscreen `0x0D`, keyboard `0x09`; features `0x21`, `0x0A` |
| 1 | `hid-generic`, `hiddev1` | `0x82` IN, `0x02` OUT | 28 bytes: vendor page `0xFF00`, report 1 in / 2 out, 63-byte payloads |
| 2 | `hid-generic` ("Mouse") | `0x83` IN | 80 bytes: 3 buttons + wheel, absolute X/Y; silent while the panel is in multitouch mode |

The device is Full Speed (12 Mbit/s), bus powered, 200 mA, `bInterval` 1. It
reports about every 10.4 ms while touched and is silent when idle.

## 2. Calibration is exact, so Y is not a mapping problem

Thirteen taps at the five calibration targets on the test page, each paired with
the frame the controller produced, give a clean linear fit:

| Axis | Fitted | Driver assumes | Residual scatter |
|---|---|---|---|
| X | `raw = 10.67 * css - 2.3` | `raw = 10.67 * css` | 3 raw units (0.3 CSS px) |
| Y | `raw = 9.99 * css + 7.9` | `raw = 10.00 * css` | 3 raw units (0.3 CSS px) |

Contact id 1 landed within ten raw units of the aimed point in all thirteen
taps, corners included. So the raw-to-screen mapping the driver uses is correct
to a third of a pixel, and the perceived "bad Y" is entirely the phantom
contacts drawn in the same column at invented rows — not error in the real
contact's position.

This closes calibration as an explanation. A single tap at `1381,491` produced
`id 1 14733,4911` (correct) alongside `id 2 14748,8413`, `id 3 14733,4247` and
`id 4 14733,1591`: same column, three invented rows, real one exactly on target.

## 3. Contact Count: laws, measured

Contact Count is a real count and it is monotone with respect to the frame, but
it is the *tracker's* count, not the frame's:

| Relationship | Session 2 (1044 reports) | Session 1 (543 reports) |
|---|---|---|
| count < tipped blocks | 0 | 0 |
| count == tipped blocks | 882 (84.5%) | 403 (74.2%) |
| count == tipped + 1 | 148 | 121 |
| count > tipped + 1 | 14 | 19 |
| reports with no tipped block at all | 35, every one count=1 | 73, count 1 or 2 |

So the controller consistently believes in one more contact than it marks. The
"no tipped block but count=1" rows are the strongest form: the tracker holds a
contact that the frame does not express at all.

This closes the original notes' open question (`GOODIX-TOUCH-DRIVER-NOTES.md`
§4.4) and kills the idea of believing the count: it is never *below* the tipped
set, so it can only ever add contacts, never remove the one that is stuck.

## 4. The phantom geometry

This is the finding that changes the diagnosis. For every report with more than
one contact, compare the spread of X across those contacts with the spread of Y:

| Session | Multi-contact reports | X spread (median / p90) | Y spread (median / p90) | X spread < 50 |
|---|---|---|---|---|
| 2 | 415 | 29 / 156 | 2374 / 6814 | 263 (63%) |
| 1 | 193 | 15 / 40 | 4637 / 7302 | 183 (95%) |

**The extra contacts are the same finger's X with a different Y.** Median X
spread of 15 raw units is about 1.4 CSS px on this panel; the median Y spread
is a quarter of the screen height. Examples:

```
[6, 8263, 6017] [3, 8220, 7031] [2, 8242, 5917]     X: 8220-8263
[6, 8289, 8451] [5, 8220, 5086] [2, 8435, 1] [1, 8237, 6769]   X: 8220-8435
[7, 4179, 3483] [6, 4175, 7861] [2, 4201, 1630] [1, 4179, 9599]  X: 4175-4201
```

And the invented Y values are not uniformly random. Y pins to the ends of its
logical range far more often than chance allows:

| Statistic | Session 2 | Session 1 |
|---|---|---|
| contacts with y in {0, 1} | 17 (1.0%) | 9 (1.1%) |
| contacts with y >= 9590 | 27 (1.6%) | 10 (1.2%) |
| contacts with x on an edge | 0 | 0 |

`9599` is `0x257F`, the Y logical maximum. A value written past the range and
clamped lands exactly there; `0`/`1` is the other end. X never pins, because X
in these frames is always the live finger's column.

## 5. The latch, in micro-structure

Two spans, both id 1 only, taken from session 2 with the page recorder
confirming `pointerdown` held 3058 ms and 4086 ms:

| Span | Duration | Reports | X range | Y range |
|---|---|---|---|---|
| rows 308–597 | 3028 ms | 290 | 6344–9281 | 6746–7007 |
| rows 620–1009 | 4073 ms | 390 | 6475–9573 | 6725–7083 |

X covers about 3000 raw units (280 CSS px); Y stays inside 60 raw units (5 CSS
px). The motion has three phases:

1. **Frozen.** The first frames are byte-identical to each other — for example
   `(6572, 6904)`, `(6572, 6904)`, then `(6571, 6891)` three times. The slot is
   not updating.
2. **Oscillation.** X then moves smoothly with no noise: consecutive deltas
   decay to a turning point and reverse, and the speed in the middle is nearly
   constant (~145 raw units per report). Turning points in the second span:
   `(6618) → (9295) → (7273) → (9196) → (6775) → (9573) → (6573)`. Measured
   period is 0.4–0.6 s depending on whether peaks or mean crossings are used.
3. **Frozen again** at the end, then gone without any trailing release frame.

A real finger produces jagged deltas with no such regularity; this is a value
being recomputed from a signal that is slowly wandering.

## 6. What fabricates the phantoms

Last revised after measuring the phantom values directly. The earlier wording
blamed a stale Y register; the data does not support that, and the correction
is recorded here rather than quietly dropped.

**Measured.** Across three captures (633 phantoms):

| Property | Result |
|---|---|
| X compared with the live contact | shared, median spread 15–29 raw units (1.5–3 px) |
| Y compared with the live contact | never close: 0% within 120 units in two captures, 2% in the third |
| Distinct phantom Y values | 66–75% unique — not a small set of stale or repeated values |
| Phantom duplicating the real contact exactly | 0 and 2, in 633 |
| Most repeated Y values | `9599` (11×), `1`, and a few mid-range values (6–8×) |
| Contact Count | never below the tipped count; `+1` in 12–14% of reports |

**What that rules out.**

- *Stale or copied positions.* Phantom Y values are mostly fresh, and never sit
  near the real contact's Y, so they are not a previous position replayed.
- *Panel or electrical noise.* Noise would disturb X as readily as Y, but X is
  the live column in essentially every phantom. Interference does not know which
  axis to leave alone.

**What it supports (inference).** The controller fabricates slot entries rather
than sensing them: each extra slot inherits the live column's X and receives an
independent, arbitrary Y. The `+1` count law is the same event seen from the
other side — the tracker holds more contacts than the frame marks tipped. Some Y
values land outside the logical range and clamp to `0`/`1` or `9599`, which is
why those three values recur.

**What is still unexplained.** Why the controller decides it has more than one
contact. Nothing available to the host reports the peak amplitudes or thresholds
that decision is made from, so the trigger cannot be identified without the
firmware.

The latch is the same failure with the finger gone: the last Y is retained while
X is recomputed from a decaying signal, so the slot never clears.

**Still not** a host decoding error, a missing report, or dropped frames: every
report is 54 bytes and the count laws hold across all captures.

## 7. The link and the power are clean

Checked because a marginal bus would produce exactly this shape of nonsense:

- 46 h of `dmesg` contains no `-71`/`-110` transfer errors, no device resets,
  no `usb_set_interface failed`, and no over-current event.
- The device reports "Bus Powered", 200 mA, and `power/control` is `on`;
  autosuspend is not in play.
- It sits behind a generic Terminus `1a40:0101` hub with no vendor strings, so
  the monitor cannot be identified from the bus.
- `hidraw0` reports `quirks = 0x1010` under the 6.12 numbering, which is
  `HID_QUIRK_HIDDEV_FORCE` (BIT 4) plus `HID_QUIRK_X_INVERT` (BIT 12). No
  mainline quirk table entry explains the second bit for this device, and it
  does not affect the userspace driver, which reads `hidraw` directly and whose
  taps land in the right place. It *would* affect anyone using the stock
  `hid-multitouch` node, so it is worth remembering. Open question.

## 8. Why no driver can fix it

The only per-contact liveness signal the descriptor provides is the Tip Switch
bit. There is no In Range, no per-contact pressure, and the count cannot
subtract. When the firmware keeps Tip Switch set on a slot it has stopped
updating, the stream contains no event that means "up". A kernel HID driver, a
different userspace filter, or another heuristic all consume the same stream.

Filtering downstream can suppress the *symptom* at the cost of dropping real
taps. That is what the previous shim did, and it is why the rewrite stopped
there.

### Continuity tracking: simulated, and worse than trusting id 1

The obvious alternative to "trust id 1" is to keep the last accepted position and
take whichever contact is nearest to it each frame. Replayed over five captures
(`probes/simulate-tracking.py`), it is worse everywhere:

| Capture | Gestures | id 1 steps >200 | Continuity >200 | id 1 worst | Continuity worst |
|---|---|---|---|---|---|
| mode 2 | 29 | 2 | 4 | 2876 | 6110 |
| mode 1 | 28 | 0 | 5 | 128 | 6076 |
| ContactMax=1 | 11 | 27 | 32 | 2016 | 5760 |
| session 2 | 18 | 28 | 31 | 2299 | 3664 |
| session 1 | 37 | 8 | 14 | 3011 | 7308 |

The reason is structural rather than a tuning problem: a phantom persists for a
few frames, so a nearest-neighbour rule hops *onto* it when it happens to land
close, then hops back when it vanishes. Anchoring to the identity instead of the
position is the more stable choice even though the identity also lies sometimes.

The two rules disagree on only 0–22 frames per capture, so there is no large pool
of cases where continuity silently rescues a wrong tap. This closes host-side
tracking as a route, not just by argument but by measurement.

Any filter that selects by position or timing should be evaluated the same way
before it is trusted: replay it over these captures and compare the jump
statistics, or it may be trading one set of wrong taps for another.

## 9. Device-level levers

Ordered by how close each is to a real repair.

### 1. Input Mode

Feature `0x21` is Input Mode / Device Index in the range 0–10. `usbmon` at bind
shows the stock driver sending:

```
S Co:3:003:0 s 21 09 0321 0000 0003  = 21 02 00
C Co:3:003:0 0 3
```

InputMode=2 (multitouch), DeviceIndex=0. Values 0 (mouse) and 1 (single touch)
select different reporting paths in the controller.

**Tested 2026-11-18: mode 1 does not change the report stream.** The write is
real — `usbmon` shows `S Co:3:003:0 s 21 09 0321 0000 0003 = 210100` completing
with status 0, and the restore `210200` likewise — so this is not a write that
got lost. The digitizer keeps emitting the same frames:

| Measure | Mode 2 (baseline) | Mode 1 (single touch) |
|---|---|---|
| reports | 314 in 34.8 s | 347 in 34.4 s |
| contacts per report | 0:58, 1:145, 2:83, 3:22, 4:5, 5:1 | 0:56, 1:182, 2:78, 3:29, 4:2 |
| multi-contact share | 35% | 31% |
| X spread median (X<50) | 13 (110 of 111) | 10 (107 of 109) |
| count laws <,=,+1,>+1 | 0 / 226 / 83 / 5 | 0 / 258 / 81 / 8 |
| Y pinned low / high | 0.2% / 2.7% | 0.7% / 2.1% |
| spans > 0.8 s | none | none |

Single-touch mode still reports two to four contacts per frame, still on one X
column with an invented Y, and still with the same count laws. So the phantom
generator is not "multitouch mode being selected", and InputMode is not a
lever for this defect.

The latch itself did not occur in either run, so this says nothing about latch
incidence — only about the frame structure.

**Mode 0 settles whether InputMode is honoured at all, and it is not.** With
InputMode=0 the digitizer still emitted report `0x0D`, still with two contacts
sharing one X column and carrying different Y values:

```
0d 21 ec0b 790a 11 ec0b f61a ...    id 2 at x=3052 y=2681
                                    id 1 at x=3052 y=6902
```

and interface 2 — the panel's own three-button mouse collection, report
`0x07` — never emitted a single frame. Modes 0, 1 and 2 are indistinguishable.
`probes/mode0-check.sh` reproduces it.

#### Contact Count Maximum is inert too

Feature `0x0A` (ContactMax) reads 10 and accepts a write without an ioctl
error, but the device keeps reporting 10 and keeps emitting multi-contact
frames: with the maximum set to 1, a 123-report capture still contained up to
seven contacts per report, with 72% of reports multi-contact. The read-back on
`0x0A` is honest (`0a 0a`), unlike `0x21`, which is how we know the value never
changed. `probes/probe-contact-max.py` and `probes/contact-max-trial.sh` are the
tools.

Both documented configuration features are therefore dead ends. The controller
cannot be reconfigured from the host, and the slot allocator that produces the
phantoms cannot be switched off. That leaves levers 2–5.

Recovery, if a mode leaves the panel silent: `probes/probe-input-mode.py 2`, or
the re-enumeration used to clean up after these tests, which is reliable and
needs no unplugging:

```
sudo sh -c 'echo 0 > /sys/bus/usb/devices/3-2.3/authorized'
sudo sh -c 'echo 1 > /sys/bus/usb/devices/3-2.3/authorized'
```

`hid-multitouch` rebinds and re-sends InputMode=2 itself. This driver used to
*not* survive it: node discovery pinned the HID instance name
(`0003:27C6:8191.0001`), which is renumbered to `.0005` on re-enumeration, so
the driver sat in "waiting for the raw HID node to appear" with touch dead
until it was restarted. Discovery now matches `HID_ID` plus `HID_PHYS`
ending in `/input0`, which is stable across resets, suspends and replugs.
Anyone resetting the panel on an older build should restart
`goodix-touch.service` afterwards.

One useful side effect: `GET_REPORT` of feature `0x21` is answered by the
*device* with its last input report — `usbmon` shows `a1 01 0321` completing
with `0d 10 26`. The unreadable read-back is firmware behaviour, not a hidraw
quirk, which is why no amount of ioctl fiddling will read the mode back.

### 2. USB reset

`USBDEVFS_RESET`, or unbinding and rebinding `usbhid`, clears a latched slot by
rebooting the controller. Recovery, not repair.

### 3. Firmware update

No public update was found for this exact device:

| Source | Result |
|---|---|
| `goodix/gdix_hid_firmware_update` v1.8.0 (latest) | GTX2/3/5/8/9, GT7868Q, Berlin, Marsei. No PID `8191`; protocol is `0x0e`/`0x0b` with 65-byte feature reports, not this device's 63-byte `0x01`/`0x02` interface. |
| Mainline Linux v6.12 and current master | No `27c6:8191` quirk. `MT_CLS_WIN_8_NO_STICKY_FINGERS` exists but is applied to a Winbond TSTP panel. |
| Microsoft Update Catalog | **Contains Goodix touch-panel firmware update packages**, one of which matches this panel's HID identity (`HID\VID_27C6&UP:FF00_U:0001`) and carries an 86 KB `tpfw.bin` plus the vendor's own read/write driver. See [`FIRMWARE-PACKAGES.md`](FIRMWARE-PACKAGES.md). |
| Panel vendor | EDID reports `EMO` / `Monitor` / serial `demoset-1`, an engineering/demo unit. No public firmware or support page found. |

The vendor is the only source of an image, and the request has to name the PID,
the EDID and the latch behaviour.

### 4. Noise, power and grounding

An unexplained oscillating contact is also what a physically noisy panel looks
like: the controller's peak detector follows interference instead of the finger.
This is worth ruling out before blaming code, and it is cheap to test:

- run the panel from a different powered hub or directly from another port;
- check the panel and the hub share a solid ground, and that the USB cable is
  shielded and short;
- test with the display backlight at a different brightness, which changes the
  switching noise the panel sees.

None of this has been tried. Note that the pane is bus powered at 200 mA; a
marginal supply degrades capacitive measurement first.

### 5. Reaching the vendor channel

Interface 1 exposes a 64-byte vendor channel (report 1 in, report 2 out, page
`0xFF00`). An earlier attempt wrote GTX5-style read frames straight to endpoint
`0x02` and got no response. The kernel's quirk state suggests a reason worth
testing: with `HID_QUIRK_NO_OUTPUT_REPORTS_ON_INTR_EP` (BIT 18) set, output
reports are delivered by control transfer, not on the interrupt endpoint. The
bit is not set on this device today, but it is the right first check if the
endpoint goes quiet again: send report 2 as a `SET_REPORT` on the control pipe
and observe with `usbmon`.

Writing to a firmware-update channel without knowing the protocol risks
bricking the panel. Do not fuzz it on the kiosk.

## 10. What a firmware reverse engineering would need

There is no image *for this panel* to analyse: nothing on the kiosk or the
laptop is a copy of its code, and the PID appears in no public package. But
two artefacts of the same family are now in hand — a sibling panel's `tpfw.bin`
and, more usefully, Goodix's own HID update/read driver for the `UP:FF00_U:0001`
collection. See [`FIRMWARE-PACKAGES.md`](FIRMWARE-PACKAGES.md).

Three routes, in order of realism:

1. **Vendor.** An image, or the vendor's Windows update tool with its bundled
   `.bin`. This is the only route that yields a repairable firmware.
2. **Read-out through the vendor channel.** Goodix updaters read flash over the
   interface-1 channel before writing it. If this firmware implements a read
   command, the image can be dumped and analysed like any other binary. The
   command set for this panel is unknown; it would have to be identified from
   the vendor's tool or captured from a Windows session running it, not guessed.
3. **Nothing else.** The USB device descriptor carries no firmware version, so
   even identifying which image would apply needs the vendor.

The request itself — the identifying facts to quote, what to ask for, how to
describe the defect in the vendor's terms, and how to capture their tool's
protocol instead of guessing at it — is in
[`VENDOR-REQUEST.md`](VENDOR-REQUEST.md).

With bytes in hand the workflow is unchanged from any other binary: open the
image, locate the report-fill routine (it writes the 5-byte blocks, the constant
`0x0101` Scan Time and the count byte), and look at the slot-clear path on
release.

## Reproduction tools

| Tool | Purpose |
|---|---|
| `probes/probe-hidraw.py` | raw report 0x0D, ids, Contact Count, lengths |
| `probes/probe-evdev.py` | driver output, press durations |
| `probes/cdp-record-touch.py` | page pointerdown/up/cancel, movement, HA action |
| `probes/analyze-session.py` | one row per gesture, moves, jumps, durations |
| `probes/analyze-splits.py` | presses split by id-1 gaps |
| `probes/stuck-detector-sim.py` | test a candidate latch detector |
| `probes/hold-reset-test.py` | re-send the mode command during a held press |
| `probes/probe-input-mode.py` | read or set InputMode, restoring a known-good mode |
| `probes/mode-trial.sh` | set a mode, capture, restore (watchdog if the panel goes silent) |
| `probes/mode-write-check.sh` | prove the mode write reaches the device, on usbmon |
| `probes/mode0-check.sh` | mode-0 discriminator: does InputMode do anything at all |
| `probes/probe-contact-max.py` | read or write Contact Count Maximum |
| `probes/contact-max-trial.sh` | set ContactMax, capture, restore |
| `probes/touch-test.html` | local 3x2 grid that shows tap and hold durations |
| `probes/probe-mouse-node.py` | watch hidraw0 and hidraw2 together |
| `probes/analyze-modes.py` | compare captures: contacts, column lock, count laws, spans |
| `probes/simulate-tracking.py` | replay id-1 versus continuity tracking over captures |
| `probes/touch-feed.py` | serve the test page plus live raw contacts and tap marks |
| `probes/cdp-nav.py`, `probes/cdp-targets.py` | navigate the kiosk browser; report each page's DOM state |
| `probes/probe-who-zeroes.py` | identify what resets uinput absinfo (Xorg) |
