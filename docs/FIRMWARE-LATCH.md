# The firmware latch — root cause of the residual hold

Written 2026-11-18 after the driver rewrite. This documents the third firmware
defect, which the original notes did not characterise, and the device-level
controls that might address it.

## Symptom

A tap occasionally becomes a multi-second press and Home Assistant's ripple
grows. The driver is delivering a held press, not failing to release one.

## Measurement

Session of 2026-11-18 10:21 AWST, with the page recorder, the driver's evdev
output and the raw HID stream captured simultaneously.

| View | Observation |
|---|---|
| Page | `pointerdown` pid=173 held **3058 ms**; pid=174 held **4086 ms** |
| Driver | press 20 **3059 ms**, press 21 **4083 ms** |
| Raw HID | id 1 present continuously for both; 908 of 1044 reports contain id 1 |

During the 3–4 s press, id 1's X swept back and forth across ~3000 raw units
(~280 CSS px) while Y stayed within ~120 raw units. One measured run:

```
(8149,6924) -> (7755,6908) -> (7342,6879) -> (6951,6837) -> (6634,6829)
    -> (6594,6832) -> (6719,6819) -> (7553,6764) -> (8429,6844)
    -> (9118,6977) -> (9280,6951) -> ... -> (6554,6952) -> ...
```

That is not a finger path. The panel streams a report every ~11 ms while it is
touched (it is silent only when idle), so this is not a sampling artifact.

The release logic itself is sound: in the first session, 39 physical gestures
produced exactly 39 driver presses and every one released. The failure is an
input that never says "up".

## Second form: id 1 ghosts

After a real lift, id 1 can reappear at a different position and sweep to an
axis limit:

```
(4186,6412) -> (4184,7032) -> (4179,9599) -> (4180,9036) -> (4181,8893)
```

Two short bursts 43 ms apart produced driver presses **10 ms apart** at
(8994,8894) and (8980,6017), which no human can produce. The rule "contact id 1
is always the real finger" is therefore false in this firmware state.

## Contact Count and report length

- Every on-wire report observed was **54 bytes** (53-byte payload + report ID).
- Contact Count was populated: values 1–6 in the latch session, 1–7 in the
  first session; never 0 while contacts were present.
- It equals the number of tipped blocks in 882/1044 reports (84%). It counts
  phantoms too, so it cannot identify the real contact.

This answers the notes' open question, but it does not solve the identity
problem.

## Why no driver can fix it

The only identity signal is id 1, and the firmware keeps it asserted while
inventing movement. A kernel HID driver, a different userspace filter, or
another heuristic all consume the same stream. The defect is in the firmware's
contact tracker; filtering downstream can only guess.

## Device-level levers

### 1. Feature `0x21` is Input Mode, not brightness

The original notes describe feature `0x21` as "keyboard/display
brightness-ish (usages 0x52/0x53)". On the Digitizer usage page, `0x52` is
**Input Mode** and `0x53` is **Device Input Mode**, two 8-bit values in the
range 0–10.

`usbmon` at driver bind shows the stock driver sending:

```
S Co:3:003:0 s 21 09 0321 0000 0003  = 21 02 00
C Co:3:003:0 0 3
```

That is `SET_REPORT` feature `0x21`, **InputMode=2 (touchscreen),
DeviceInputMode=0**. A `GET_REPORT` of `0x0A` (Contact Count Max) returns
`0a 0a`, so the control path is sound.

Re-sending `21 02 00` may reinitialise the contact tracker. That is being
tested with `probes/hold-reset-test.py`; it is a device-state reset, not a
filter.

Two practical notes:
- `HIDIOCGFEATURE` on hidraw for report `0x21` returns the last *input* report
  (`0d ...`), not the feature. The stock driver only ever sets it, so do not
  trust a hidraw read-back.
- usbfs control transfers return `EBUSY` while usbhid owns the interface.
  `usbmon` is the reliable way to observe the command.

### 2. USB reset

`USBDEVFS_RESET` (or unbinding/rebinding usbhid) reboots the controller state.
It will clear a latched contact, at the cost of a brief re-enumeration. This is
recovery, not repair.

### 3. Firmware update

No public update was found for this exact device:

| Source | Result |
|---|---|
| `goodix/gdix_hid_firmware_update` v1.8.0 (latest) | GTX2/3/5/8/9, GT7868Q, Berlin, Marsei. No PID `8191`; protocol is `0x0e`/`0x0b` with 65-byte feature reports, not this device's 63-byte `0x01`/`0x02` interface. |
| Mainline Linux v6.12 and current master | No `27c6:8191` quirk. `MT_CLS_WIN_8_NO_STICKY_FINGERS` exists but is applied to a Winbond TSTP panel. |
| Microsoft Update Catalog | No result for `USB\VID_27C6&PID_8191`. |
| Panel vendor | EDID reports `EMO` / `Monitor` / serial `demoset-1`, an engineering/demo unit. No public firmware or support page found. |

The only true repair is a firmware image/tool for `27c6:8191` from the panel
vendor or seller.

### 4. Other input modes

Values other than 2 may select a mode that does not latch, or one that exposes
raw data on the vendor interface. This is untested and should be treated as an
experiment: a wrong mode can leave the panel unusable until a USB reset.

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
| `probes/probe-who-zeroes.py` | identify what resets uinput absinfo (Xorg) |
