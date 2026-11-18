#!/usr/bin/env python3
"""Re-emit the Goodix panel as a clean single-touch screen, from raw HID.

Why this reads HID and not evdev
    The panel's firmware is defective in ways the kernel cannot see past:

      * ContactCount is always 0. The field is mandatory and never set, so the
        driver cannot use it to know how many contacts are real.
      * A single tap arrives as a cohort of 2-6 contacts in one report. The
        real finger is contact id 1; ids 2-6 are phantoms carrying the real
        finger's X with a garbage Y (or vice versa). One measured phantom
        swept Y from 3583 to 9237 in 80ms.
      * Phantom contacts also appear *alone*, in their own report, when no
        other contact is active. Measured: 3 of 201 solo reports were a
        phantom, contact id 5. A rule that trusts "a contact born alone" lets
        these through, and one of them lands as a spurious touch somewhere
        else on the screen mid-gesture - which cancels the tap the user is
        making, and is why a tapped button only sometimes activates.

    hid-multitouch burns the contact id before evdev sees anything: it assigns
    slots and synthesises its own tracking ids, so by userspace there is no
    way to tell the real contact from a phantom. The identity exists only in
    the report itself, so this daemon reads /dev/hidraw0 and decides there.

The rule
    Trust the tipped contact with the lowest ContactIdentifier in each report.
    On this panel that is id 1 for every real touch: measured over 14
    gestures, 198 of 198 solo real reports carried id 1, and every multi
    report had id 1 present as the lowest. The only solo reports that did not
    carry id 1 were phantoms (id 5).

Report 0x0D layout, from the descriptor
    five finger blocks, 5 bytes each:
        byte 0  bit 0 TipSwitch, bits 4-7 Contact Identifier
        bytes 1-2  X (16-bit LE)      bytes 3-4  Y (16-bit LE)
    then Scan Time (16-bit LE) and Contact Count (8-bit).

The panel is also noisy: fuzz=0 on every axis, and a finger held still drifts
12-22 CSS px, past Chromium's 8 px touch slop, so taps became scrolls and were
cancelled. The virtual device this daemon creates gets fuzz set on its axes,
so that noise is removed by the input core before libinput or Chromium see it.

The raw evdev node is kept grabbed (but never read) so nothing else - notably
X - can open it and see the phantoms directly.

If this process exits, the grab is released and the raw device works again,
phantoms and all.
"""

import fcntl
import os
import signal
import select
import struct
import sys
import time

from evdev import AbsInfo, InputDevice, InputEvent, UInput, ecodes as e

SRC_HID = os.environ.get("TOUCH_HID", "/dev/hidraw0")
SRC_EVDEV = os.environ.get("TOUCH_EVDEV", "/dev/input/by-id/usb-TOUCH_TOUCH-event-if00")
REPORT_ID = 0x0D
# Report 0x0D carries TEN finger blocks of 5 payload bytes each, then a
# 16-bit Scan Time and an 8-bit Contact Count:
#
#   block n   at 5*n .. 5*n+4
#   Scan Time at 50..51
#   Count     at 52
#
#   payload 53 bytes, or 54 on the wire including the report id.
#
# The descriptor confirms the count: ten `09 22 a1 02 09 42` collections in
# the 0x0D segment. An earlier version of this decoder read five, which made
# blocks 6-10 invisible and caused the byte at offset 27 to be mistaken for
# Contact Count (it is part of block 6).
FINGERS = 10
SCAN_TIME_OFFSET = FINGERS * 5          # 50
CONTACT_COUNT_OFFSET = SCAN_TIME_OFFSET + 2   # 52
REPORT_LEN = CONTACT_COUNT_OFFSET + 1   # 53 payload bytes
FUZZ = int(os.environ.get("TOUCH_FUZZ", "80"))
# How long id 1 may be absent before a live press is released. The panel
# omits id 1 from some phantom-only frames even while the finger is down, so
# this cannot be zero, but it must stay well under a tap so the release lands
# before the page's hold timer would fire.
GRACE_MS = float(os.environ.get("TOUCH_GRACE_MS", "30"))
NAME = "TOUCH TOUCH Filtered"
DEBUG = os.environ.get("TOUCH_FILTER_DEBUG", "") not in ("", "0")

X_MAX, Y_MAX = 16383, 9599
EVIOCGRAB = 0x40044590


def log(msg):
    print(time.strftime("%F %T"), msg, flush=True)


def decode(data):
    """Parse report 0x0D.

    Returns (contacts, contact_count), where contacts is a list of
    (contact_id, x, y) for blocks with a live tip, and contact_count is the
    firmware's own count byte (informational - see the note in the driver
    notes about whether it can be trusted).
    """
    out = []
    for n in range(FINGERS):
        base = 5 * n
        b0 = data[base]
        if not (b0 & 0x01):
            continue
        out.append(((b0 >> 4) & 0x0F,
                    data[base + 1] | (data[base + 2] << 8),
                    data[base + 3] | (data[base + 4] << 8)))
    count = data[CONTACT_COUNT_OFFSET] if len(data) > CONTACT_COUNT_OFFSET else None
    return out, count


class Emitter:
    """One virtual touchscreen, driven by the trusted contact stream."""

    def __init__(self):
        caps = {
            e.EV_ABS: [
                (e.ABS_MT_SLOT, AbsInfo(0, 0, 9, 0, 0, 0)),
                (e.ABS_MT_TRACKING_ID, AbsInfo(0, 0, 65535, 0, 0, 0)),
                (e.ABS_MT_POSITION_X, AbsInfo(0, 0, X_MAX, 0, 0, 0)),
                (e.ABS_MT_POSITION_Y, AbsInfo(0, 0, Y_MAX, 0, 0, 0)),
                (e.ABS_X, AbsInfo(0, 0, X_MAX, 0, 0, 0)),
                (e.ABS_Y, AbsInfo(0, 0, Y_MAX, 0, 0, 0)),
            ],
            e.EV_KEY: [e.BTN_TOUCH],
        }
        self.ui = UInput(caps, name=NAME, input_props=[e.INPUT_PROP_DIRECT])
        self.tid = 0
        self.down = False
        self.sent = (None, None)

    def _w(self, typ, code, value):
        self.ui.write_event(InputEvent(0, 0, typ, code, value))

    def press(self, x, y):
        self.tid = (self.tid + 1) & 0xFFFF or 1
        self._w(e.EV_ABS, e.ABS_MT_SLOT, 0)
        self._w(e.EV_ABS, e.ABS_MT_TRACKING_ID, self.tid)
        self._w(e.EV_ABS, e.ABS_MT_POSITION_X, x)
        self._w(e.EV_ABS, e.ABS_MT_POSITION_Y, y)
        self._w(e.EV_KEY, e.BTN_TOUCH, 1)
        self._w(e.EV_ABS, e.ABS_X, x)
        self._w(e.EV_ABS, e.ABS_Y, y)
        self._w(e.EV_SYN, e.SYN_REPORT, 0)
        self.down = True
        self.sent = (x, y)

    def move(self, x, y):
        """Report movement, ignoring changes smaller than the noise floor.

        A uinput device will not take an EVIOCSABS fuzz setting (the ioctl is
        accepted but the event node keeps advertising fuzz=0), so the deadband
        the kernel would normally apply is applied here instead. Same rule:
        an axis is emitted only once it has moved FUZZ units from the last
        value reported for it.
        """
        if not self.down:
            return
        sx, sy = self.sent
        emit_x = sx is None or abs(x - sx) >= FUZZ
        emit_y = sy is None or abs(y - sy) >= FUZZ
        if not (emit_x or emit_y):
            return
        self._w(e.EV_ABS, e.ABS_MT_SLOT, 0)
        if emit_x:
            self._w(e.EV_ABS, e.ABS_MT_POSITION_X, x)
            self._w(e.EV_ABS, e.ABS_X, x)
        if emit_y:
            self._w(e.EV_ABS, e.ABS_MT_POSITION_Y, y)
            self._w(e.EV_ABS, e.ABS_Y, y)
        self._w(e.EV_SYN, e.SYN_REPORT, 0)
        self.sent = (x if emit_x else sx, y if emit_y else sy)

    def release(self):
        if not self.down:
            return
        self._w(e.EV_ABS, e.ABS_MT_SLOT, 0)
        self._w(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1)
        self._w(e.EV_KEY, e.BTN_TOUCH, 0)
        self._w(e.EV_SYN, e.SYN_REPORT, 0)
        self.down = False
        self.sent = (None, None)

    def close(self):
        self.release()
        self.ui.close()


class Panel:
    """Tracks which reported contact is the real finger."""

    def __init__(self, out, grace_secs=0.10):
        self.out = out
        self.grace_secs = grace_secs
        self.current = None     # contact id we are currently tracking
        self.last_real = None   # monotonic time id 1 was last reported
        self.presses = 0
        self.phantom_frames = 0

    def feed(self, contacts, now):
        """contacts: list of (contact_id, x, y) with a live tip.

        The real finger is contact id 1. Anything else is a phantom: this
        panel's firmware invents extra contacts carrying the real finger's X
        with a garbage Y, and sometimes reports one entirely on its own.
        """
        if not contacts:
            # An explicit empty report is the panel saying the finger is up.
            # Release at once: any delay lengthens every tap, and a press that
            # lasts past the page's hold threshold turns a tap into a hold -
            # the expanding blue circle. Measured on this page, a press over
            # ~190ms stops producing a tap at all.
            if self.current is not None:
                self.out.release()
                self.current = None
                self.last_real = None
            return

        real = [c for c in contacts if c[0] == 1]

        if not real:
            # Only phantoms in this report. That is also how a tap usually
            # ends - the finger lifts and a phantom lingers - but it can also
            # happen for a frame or two while the finger is genuinely down, so
            # absence has to persist briefly before the press is dropped.
            self.phantom_frames += 1
            if DEBUG:
                log("  phantom-only report %s" % (contacts,))
            if self.current is not None and (now - self.last_real) >= self.grace_secs:
                self.out.release()
                self.current = None
                self.last_real = None
            return

        cid, x, y = real[0]
        self.last_real = now
        if self.current is None:
            self.current = cid
            self.presses += 1
            self.out.press(x, y)
            log("press %d (contact id %d at %d,%d)" % (self.presses, cid, x, y))
        elif self.out.down:
            self.out.move(x, y)

    def tick(self, now):
        """Expire a press whose finger has gone and stopped reporting.

        The panel only sends a report when something changes, so after a
        finger lifts there may be no further reports to trigger the release.
        """
        if self.current is not None and self.last_real is not None \
                and (now - self.last_real) >= self.grace_secs:
            self.out.release()
            self.current = None
            self.last_real = None


def open_hid():
    while True:
        try:
            fd = os.open(SRC_HID, os.O_RDONLY)
            log("reading raw reports from %s" % SRC_HID)
            return fd
        except OSError as exc:
            log("waiting for %s: %r" % (SRC_HID, exc))
            time.sleep(3)


def grab_raw():
    """Hold the raw evdev node so X cannot read the phantoms itself.

    Never read from it: the whole point is to bypass hid-multitouch's view.
    The grab is what keeps X from opening the device a second time.
    """
    while True:
        try:
            dev = InputDevice(SRC_EVDEV)
            dev.grab()
            log("grabbed %s (held shut, not read)" % SRC_EVDEV)
            return dev
        except OSError as exc:
            log("waiting to grab %s: %r" % (SRC_EVDEV, exc))
            time.sleep(3)


def main():
    out = Emitter()
    log("virtual device: %s (%s), noise floor %d raw units" % (out.ui.device.path, NAME, FUZZ))
    raw = grab_raw()
    hid = open_hid()
    panel = Panel(out, GRACE_MS / 1000.0)

    def shutdown(signum, frame):
        log("signal %d, exiting" % signum)
        try:
            out.close()
        finally:
            try:
                raw.ungrab()
                raw.close()
            except Exception:
                pass
            sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log("policy: the real finger is HID contact id 1; anything else is a "
        "phantom. A press is released when id 1 has been absent for %dms."
        % int(GRACE_MS))
    while True:
        try:
            r, _, _ = select.select([hid], [], [], 0.01)
            now = time.monotonic()
            if not r:
                panel.tick(now)
                continue
            data = os.read(hid, 64)
            if not data or data[0] != REPORT_ID:
                continue
            payload = data[1:]
            if len(payload) < REPORT_LEN:
                # Short report: not something we can read safely. The device
                # sends 54-byte reports; anything less means the layout does
                # not match what this decoder expects.
                log("ignoring short report 0x%02X (%d payload bytes, expected %d)"
                    % (data[0], len(payload), REPORT_LEN))
                continue
            contacts, count = decode(payload)
            if DEBUG and count is not None:
                log("  report: %d tipped block(s), firmware count=%d"
                    % (len(contacts), count))
            panel.feed(contacts, now)
        except OSError as exc:
            log("read error: %r" % (exc,))
            out.release()
            panel.current = None
            try:
                os.close(hid)
            except Exception:
                pass
            hid = open_hid()


if __name__ == "__main__":
    main()
