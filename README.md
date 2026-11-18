# Goodix 27c6:8191 kiosk touchscreen driver

A rewrite of the kiosk's touch path so the fix lives in the driver, not in a
chain of userspace workarounds.

The investigation and measurements are in
[`docs/GOODIX-TOUCH-DRIVER-NOTES.md`](docs/GOODIX-TOUCH-DRIVER-NOTES.md).
This repository contains the rewritten driver, the tests that encode the
observed firmware behaviour, and the deployment pieces.

## What was wrong

The panel's firmware reports one tap as 2-10 contacts, invents extra contact
ids, leaves phantoms behind after the finger lifts, and drifts 12-22 CSS px
while the finger is held still. `hid-multitouch` discards the firmware's
Contact Identifier, so the only field that identifies the real finger is gone
by the time userspace sees an evdev event.

A third defect was found later: the controller can **latch contact id 1** and
stream a synthetic X sweep for seconds, so the finger appears to stay down
even after it is lifted. Because the firmware never clears id 1, no driver can
infer the release from the report stream. The evidence and the device-level
controls that might clear it are in
[`docs/FIRMWARE-LATCH.md`](docs/FIRMWARE-LATCH.md).

The previous Python shim read raw HID, which was the right layer, but:

| Problem | Effect |
|---|---|
| decoded only 5 of the 10 finger blocks | ids 6-10 invisible; the byte at offset 27 read as Contact Count |
| release condition / grace churn | 100 ms grace made every tap look like a hold; 30 ms now works but was still a shim |
| no reconnect path for the raw grab | a USB re-enumeration could leave X reading the raw panel |

## What this driver does differently

- **All ten blocks**, decoded tolerantly. A short report is decoded as far as
  it goes instead of killing touch.
- **Trusts HID contact id 1** as the real finger (198/198 solo real reports,
  every multi-contact report); solo phantoms (id 5) never start a press.
  Caveat: the firmware can also latch or ghost id 1 after a real lift; see
  `docs/FIRMWARE-LATCH.md`. That failure is not recoverable downstream.
- **Releases on the real contact disappearing**, not on "no contacts": an
  explicit empty report releases immediately; absences are released after
  `TOUCH_GRACE_MS` (default 30 ms, far below the ~170 ms tap window).
- **The noise floor is applied in-process.** `fuzz=80` is also written into
  the absinfo at creation (`UI_ABS_SETUP` does work), but Xorg opens the new
  device about 100 ms later and resets it to 0 - measured with
  `probes/probe-who-zeroes.py`. The kernel therefore cannot be relied on to
  filter the drift; the driver applies the same 80-unit deadband against the
  last emitted value, which is what actually removes it. The absinfo value is
  set as a best-effort hint and logged after Xorg has had its turn.
- **Capability-based discovery** of `/dev/hidraw*` and the raw evdev node, so
  a changed event number or a second device with the same name cannot select
  the wrong node.
- **Reconnect handling** for both the HID node and the raw grab.
- **`INPUT_PROP_DIRECT`**, single-touch output, `BTN_TOUCH`, MT slot 0, fresh
  tracking id per press.
- Logs each payload length once, so a firmware that disagrees with its own
  descriptor is visible without spam.

## Layout

```
src/goodix-touch-driver.py     the driver (installed to /usr/local/bin)
tests/test-touch-hid.py        decoding + trust rule (from the original work)
tests/test-touch-release.py    press end + press length (from the original work)
tests/test-touch-driver.py     guarantees added by this rewrite
probes/probe-hidraw.py         read-only report recorder (Contact Count, ids)
probes/probe-evdev.py          read-only driver-output recorder
probes/cdp-record-touch.py     page pointer/HA action recorder
probes/analyze-session.py      reconstruct gestures from the captures
probes/analyze-splits.py       find presses split by id-1 gaps
probes/stuck-detector-sim.py   test candidate latch detectors
probes/hold-reset-test.py      re-send the controller mode command on a hold
probes/probe-who-zeroes.py     show that Xorg resets uinput absinfo
probes/probe-uinput-fuzz.py    prove absinfo fuzz survives UI_ABS_SETUP
docs/FIRMWARE-LATCH.md         root cause, evidence, device-level levers
deploy/goodix-touch.service    systemd unit
deploy/install.sh              install + switch + verify (auto-rollback)
deploy/rollback.sh             back to the old shim
deploy/verify.sh               service, device, property and grab checks
```

## Test

Needs Python 3 and `python3-evdev` (present on the kiosk).

```sh
cd tests
./run.sh ../src/goodix-touch-driver.py
```

The two original HID suites and the new one run without touching the panel:
they replace `UInput` and the clock.

`test-touch-cohort.py`, `test-touch-storm.py` and `test-touch-drift.py` in
`tests/` target the superseded evdev-level `Filter` API, not this driver.
They are kept for history; see the notes for why that layer could not work.

## Install on the kiosk

Copy the repository to the kiosk and run, as root:

```sh
sudo ./deploy/install.sh
```

This installs the driver and unit, disables `touch-filter.service` (it is not
removed), enables `goodix-touch.service`, then runs `deploy/verify.sh`. If
verification fails it rolls back automatically.

Roll back at any time:

```sh
sudo ./deploy/rollback.sh
```

## Contact Count: measured

The firmware's Contact Count byte is populated (values 1-7 across two
sessions, never 0 while contacts are present) and every report is 54 bytes on
the wire. It equals the number of tipped blocks in 74-84% of reports and
counts phantoms, so it is decoded and logged but **not** used to choose the
real contact. See `docs/FIRMWARE-LATCH.md`.

## Known trade-offs

- The panel is change-driven, so silence is ambiguous: it can mean "finger
  still" or "finger gone". The driver prefers ending a press. A motionless
  long press with no reports at all is therefore cut at 30 ms; raise
  `TOUCH_GRACE_MS` if that matters, but measure against the page first.
- Xorg resets the virtual device's absinfo `fuzz` to 0 shortly after it
  appears. The driver does not depend on it; the noise floor is applied
  in-process. `probes/probe-who-zeroes.py` shows the reset and names the
  process holding the node.
- The 80-unit deadband suppresses small wobble (about 8 CSS px). A large
  monotonic drift is eventually reported once it exceeds the deadband; the
  tests and the notes pin 80 because anything larger would itself exceed
  Chromium's touch slop when it finally fires.
- The controller can latch id 1 for 3-4 s and stream a synthetic sweep, or
  ghost it after a lift. No driver can see the missing release; the only
  lever is to reset the controller state. No public firmware update exists for
  PID 8191 (see `docs/FIRMWARE-LATCH.md`).
- The driver keeps `hid-multitouch` bound and grabs its evdev node rather
  than unbinding it. That preserves the stylus and keyboard nodes; the cost is
  that both drivers decode the reports.

## Update availability

No public update was found for this controller/panel: Goodix's official HID
updater v1.8.0 does not list PID `8191` and uses a different protocol;
mainline Linux has no `27c6:8191` quirk; the Microsoft Update Catalog has no
entry for the hardware ID; the panel EDID is `EMO` / `Monitor` / `demoset-1`.
A firmware image or tool would have to come from the panel vendor. Details in
[`docs/FIRMWARE-LATCH.md`](docs/FIRMWARE-LATCH.md).

## Why not a kernel module

A kernel HID driver is the natural long-term home for this, and the notes
describe its shape. The kiosk currently has no kernel headers, compiler or
DKMS, so that is a larger change than the driver itself. This userspace
driver still owns the device properly: it reads the raw reports, keeps the
contact id, sets the noise floor at registration and emits a clean device.
It is the same policy a kernel driver would apply, with a rollback that is one
command.
