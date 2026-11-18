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

## 4. Still not measured

`Contact Count` (payload offset 52) is decoded and logged, but has not yet
been observed with the correct ten-block layout while a real finger is on the
panel. `probes/probe-hidraw.py` records it read-only alongside the running
driver:

```sh
sudo python3 probes/probe-hidraw.py 120
# touch the screen during the window
cat /tmp/goodix-probe-summary.json
```

If it turns out to identify the real contact set, the trust rule can be
changed in one place (`Panel.feed`). Until then, id 1 remains the identity
signal.
