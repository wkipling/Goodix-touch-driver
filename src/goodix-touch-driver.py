#!/usr/bin/env python3
"""Driver for the Goodix 27c6:8191 kiosk touchscreen (raw HID -> clean evdev).

Why this exists
---------------
The panel's firmware is defective in two independent ways that produce the
kiosk symptom "a tap sometimes behaves as a long press":

  1. A press that never ends.  The firmware invents extra contacts (ids 2-6)
     in the same report as the real finger, and keeps them after the real
     finger lifts.  A release rule of "no contacts at all" therefore never
     fires, and the page's hold timer wins.

  2. A press that looks too long, or is cancelled.  The panel reports a
     finger held still as drifting 12-22 CSS px.  Chromium's touch slop is
     8 CSS px, so the tap becomes a scroll and is cancelled; and any press
     over ~180ms shows Home Assistant's hold ripple.

The stock hid-multitouch driver cannot fix either: it discards the firmware's
Contact Identifier when it assigns its own MT slots, so by the time events
reach userspace there is no way to tell the real finger from a phantom.

This driver therefore owns the device at the only layer where the facts still
exist: it reads report 0x0D from the raw HID node and emits a single-contact
touchscreen of its own.

What it does
------------
* Decodes all ten finger blocks of report 0x0D (the previous shim read five).
* Trusts HID contact id 1 as the real finger.  Measured on this panel:
  198/198 solo real reports carry id 1, every multi-contact report includes
  id 1, and the only solo reports that are not id 1 are phantoms (id 5).
* Releases on the real contact disappearing, not on "no contacts": an explicit
  empty report releases at once, and a press whose id 1 stops arriving is
  released after GRACE_MS (30ms).  A tick catches the change-driven panel
  going silent after a lift.
* Applies a noise floor.  `fuzz=80` is put in the absinfo passed to
  UInput() at creation, which uses UI_ABS_SETUP and does take effect.  But
  Xorg opens the new device about 100ms later and rewrites its absinfo to
  fuzz=0 (measured on this kiosk with probes/probe-who-zeroes.py), so the
  kernel cannot be relied on to filter the drift.  The driver therefore
  applies the same 80-unit deadband itself, relative to the last emitted
  value, which is what the previous shim did.  The absinfo setting is kept
  as a best-effort hint and its post-X value is logged.
* Grabs the raw multitouch evdev node (without reading it) so X cannot see
  the phantoms directly.
* Preserves INPUT_PROP_DIRECT so libinput treats it as a touchscreen.
* Stays single-touch; Chromium runs --disable-pinch, so a second finger buys
  nothing and resolving id 1 is what matters.

Deliberate trade-offs
---------------------
The panel only reports changes, so "silence" is ambiguous: it can mean the
finger is still or the finger is gone.  A press is released after GRACE_MS of
no id 1, which prioritises ending presses over preserving a perfectly
motionless long press.  GRACE_MS is deliberately far below the ~170ms tap
window.  If long holds with no motion turn out to matter, raise TOUCH_GRACE_MS,
but measure against the page first: every millisecond is added to a tap.

The firmware's Contact Count byte is decoded and available for diagnostics
(see probes/probe-hidraw.py), but is not trusted for the decision: it does not
say *which* block is real, and a solo phantom can carry any count.  Id 1 is
the identity signal until measurement says otherwise.
"""

import errno
import fcntl
import glob
import os
import select
import signal
import struct
import sys
import time

from evdev import AbsInfo, InputDevice, InputEvent, UInput, ecodes as e

# ---------------------------------------------------------------------------
# Device and report geometry (measured, see docs/GOODIX-TOUCH-DRIVER-NOTES.md)
# ---------------------------------------------------------------------------
VENDOR, PRODUCT = 0x27C6, 0x8191
# Identity of the multitouch interface, by USB identity and interface number.
# The HID instance name ("0003:27C6:8191.0001") carries a suffix that is
# renumbered on every re-enumeration, so matching on it leaves the driver
# waiting for a node that cannot come back after a USB reset, suspend or replug.
HID_ID = "0003:%08X:%08X" % (VENDOR, PRODUCT)
HID_PHYS_SUFFIX = "/input0"

REPORT_ID = 0x0D
FINGERS = 10
BLOCK = 5
SCAN_TIME_OFFSET = FINGERS * BLOCK              # 50
CONTACT_COUNT_OFFSET = SCAN_TIME_OFFSET + 2     # 52
REPORT_LEN = CONTACT_COUNT_OFFSET + 1           # 53 payload bytes, 54 on wire

X_MAX, Y_MAX = 16383, 9599
PRIMARY_ID = 1

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------
FUZZ = int(os.environ.get("TOUCH_FUZZ", "80"))
GRACE_MS = float(os.environ.get("TOUCH_GRACE_MS", "30"))
TICK_SECS = 0.01
NAME = "TOUCH TOUCH Filtered"
DEBUG = os.environ.get("TOUCH_FILTER_DEBUG", "") not in ("", "0")

# Ghost-press guard.  The panel sometimes emits two presses a few tens of
# milliseconds apart, at the same X but different rows, which no hand can
# produce: both instances in the captures land a tap in the wrong cell.  A
# press that starts within GHOST_GAP_MS of the last report of the previous
# press, in the same column (GHOST_DX) but a different row (GHOST_DY), is
# dropped.  The gap is measured from when the finger was last seen, not from
# the moment the release was emitted, because the latter is an artefact of the
# grace period rather than a physical event.
#
# Replayed over five captures this drops 2 presses out of 131 - both of them
# the impossible pairs already documented - and touches no legitimate tap.
# The nearest legitimate press in the same column is 787 ms away, so the
# threshold sits in an empty band (docs/FINDINGS-ADDENDUM.md, section 9).
# It is a targeted filter, not a repair: the phantom is still in the stream,
# and the latch is untouched.  Set TOUCH_GHOST_GUARD=0 to disable.
GHOST_GUARD = os.environ.get("TOUCH_GHOST_GUARD", "1") not in (
    "", "0", "no", "off")
GHOST_GAP_MS = float(os.environ.get("TOUCH_GHOST_GAP_MS", "120"))
GHOST_DX = int(os.environ.get("TOUCH_GHOST_DX", "120"))
GHOST_DY = int(os.environ.get("TOUCH_GHOST_DY", "400"))

# The by-id name is stable across re-enumeration; the capability scan is the
# fallback for a machine whose by-id links have not been populated yet.
SRC_HID = os.environ.get("TOUCH_HID", "")
SRC_EVDEV = os.environ.get("TOUCH_EVDEV",
                           "/dev/input/by-id/usb-TOUCH_TOUCH-event-if00")

_ABS_WITH_FUZZ = (e.ABS_X, e.ABS_Y, e.ABS_MT_POSITION_X, e.ABS_MT_POSITION_Y)
_RUNNING = True


def log(msg):
    print(time.strftime("%F %T"), msg, flush=True)


def debug(msg):
    if DEBUG:
        log(msg)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------
def decode(data):
    """Parse report 0x0D.

    Tolerant by design: decode every whole block actually present.  Refusing
    a short report would kill touch entirely, a worse failure than decoding
    fewer contacts than the descriptor promises.

    Returns (contacts, contact_count) where contacts is a list of
    (contact_id, x, y) for blocks with a live tip, and contact_count is the
    firmware's count byte, or None if the payload is too short to hold it.
    """
    out = []
    for n in range(FINGERS):
        base = n * BLOCK
        if base + BLOCK > len(data):
            break
        b0 = data[base]
        if not (b0 & 0x01):
            continue
        out.append(((b0 >> 4) & 0x0F,
                    data[base + 1] | (data[base + 2] << 8),
                    data[base + 3] | (data[base + 4] << 8)))
    count = (data[CONTACT_COUNT_OFFSET]
             if len(data) > CONTACT_COUNT_OFFSET else None)
    return out, count


# ---------------------------------------------------------------------------
# Output device
# ---------------------------------------------------------------------------
def abs_caps(fuzz=FUZZ):
    """Capabilities for the virtual touchscreen, with the noise floor set.

    The fuzz values are part of the absinfo passed to UInput(), which is what
    makes them real.  Setting them afterwards with EVIOCSABS does not work on
    a uinput device.
    """
    return {
        e.EV_ABS: [
            (e.ABS_MT_SLOT, AbsInfo(0, 0, 9, 0, 0, 0)),
            (e.ABS_MT_TRACKING_ID, AbsInfo(0, 0, 65535, 0, 0, 0)),
            (e.ABS_MT_POSITION_X, AbsInfo(0, 0, X_MAX, fuzz, 0, 0)),
            (e.ABS_MT_POSITION_Y, AbsInfo(0, 0, Y_MAX, fuzz, 0, 0)),
            (e.ABS_X, AbsInfo(0, 0, X_MAX, fuzz, 0, 0)),
            (e.ABS_Y, AbsInfo(0, 0, Y_MAX, fuzz, 0, 0)),
        ],
        e.EV_KEY: [e.BTN_TOUCH],
    }


def get_abs(fd, code):
    buf = bytearray(24)
    fcntl.ioctl(fd, (2 << 30) | (24 << 16) | (0x45 << 8) | (0x40 + code), buf)
    return struct.unpack("iiiiii", bytes(buf))


class Emitter:
    """One virtual touchscreen, driven by the trusted contact stream."""

    def __init__(self):
        self.ui = UInput(abs_caps(), name=NAME,
                         input_props=[e.INPUT_PROP_DIRECT])
        self.tid = 0
        self.down = False
        self.sent = (None, None)
        self._logged_path = getattr(getattr(self.ui, "device", None),
                                    "path", None)
        self.log_absinfo("at creation")

    def log_absinfo(self, stage):
        """Report the fuzz actually advertised, through a fresh open.

        Reading absinfo back through the uinput handle proves nothing.  Note
        that Xorg rewrites this to zero shortly after the device appears; the
        driver's filtering does not depend on it.
        """
        path = getattr(getattr(self.ui, "device", None), "path", None)
        if not path:
            return
        try:
            fd = os.open(path, os.O_RDONLY)
            try:
                seen = {code: get_abs(fd, code)[3] for code in _ABS_WITH_FUZZ}
            finally:
                os.close(fd)
        except OSError as exc:
            log("could not read absinfo on %s: %r" % (path, exc))
            return
        values = sorted(set(seen.values()))
        log("absinfo fuzz %s on %s: %s%s"
            % (stage, path,
               values[0] if len(values) == 1 else seen,
               "" if values == [FUZZ]
               else " (Xorg resets this; the deadband is applied in-process)"))

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
        """Report movement, applying the noise floor in-process.

        An axis is emitted only once it has moved FUZZ units from the value
        last reported for it.  Comparing against the last *emitted* value (not
        the previous sample) is what stops slow stationary drift from
        accumulating into a jump: the reference stays put until real travel
        exceeds the deadband.
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
        try:
            self.release()
        finally:
            self.ui.close()


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------
class Panel:
    """Tracks which reported contact is the real finger."""

    def __init__(self, out, grace_secs=None, ghost_guard=None,
                 ghost_gap_ms=None, ghost_dx=None, ghost_dy=None):
        self.out = out
        self.grace_secs = (GRACE_MS / 1000.0) if grace_secs is None \
            else grace_secs
        self.current = None     # contact id being tracked during a press
        self.last_real = None   # monotonic time id 1 was last reported
        self.presses = 0
        self.phantom_frames = 0
        self.solo_phantoms = 0
        self.unexpected_ids = set()

        # ghost-press guard state
        self.guard = GHOST_GUARD if ghost_guard is None else ghost_guard
        self.guard_gap_ms = GHOST_GAP_MS if ghost_gap_ms is None \
            else ghost_gap_ms
        self.guard_dx = GHOST_DX if ghost_dx is None else ghost_dx
        self.guard_dy = GHOST_DY if ghost_dy is None else ghost_dy
        self.suppressed = False  # current gesture dropped by the guard
        self.press_origin = None  # (x, y) where the current press began
        self.last_press = None   # {t_end, x, y} of the last accepted press
        self.ghost_presses = 0

    def _is_ghost(self, x, y, now):
        """True when a press has the shape of a phantom rather than a tap."""
        if not self.guard or self.last_press is None:
            return False
        gap_ms = (now - self.last_press["t_end"]) * 1000.0
        if gap_ms < 0 or gap_ms > self.guard_gap_ms:
            return False
        return (abs(x - self.last_press["x"]) <= self.guard_dx and
                abs(y - self.last_press["y"]) >= self.guard_dy)

    def _release(self):
        if self.current is not None and not self.suppressed and \
                self.last_real is not None and self.press_origin is not None:
            self.last_press = {"t_end": self.last_real,
                               "x": self.press_origin[0],
                               "y": self.press_origin[1]}
        self.suppressed = False
        self.press_origin = None
        self.out.release()
        self.current = None
        self.last_real = None

    def feed(self, contacts, now):
        """contacts: list of (contact_id, x, y) with a live tip."""
        if not contacts:
            # An explicit empty report is the panel saying the finger is up.
            # Release at once: any delay lengthens every tap, and a press
            # that lasts past the page's hold threshold becomes a hold.
            if self.current is not None:
                debug("  empty report -> release")
                self._release()
            return

        real = [c for c in contacts if c[0] == PRIMARY_ID]

        if not real:
            # Only phantoms in this report.  This is also how a tap usually
            # ends - the finger lifts and a phantom lingers - but it can
            # happen for a frame or two while the finger is genuinely down,
            # so absence has to persist briefly before the press is dropped.
            self.phantom_frames += 1
            if len(contacts) == 1:
                self.solo_phantoms += 1
                self.unexpected_ids.add(contacts[0][0])
            debug("  phantom-only report %s" % (contacts,))
            if self.current is not None and \
                    (now - self.last_real) >= self.grace_secs:
                debug("  id 1 absent for %dms -> release"
                      % int((now - self.last_real) * 1000))
                self._release()
            return

        cid, x, y = real[0]
        self.last_real = now
        if self.current is None:
            if self._is_ghost(x, y, now):
                self.suppressed = True
                self.current = cid
                self.press_origin = (x, y)
                self.ghost_presses += 1
                log("ghost press dropped (id %d at %d,%d, %.0f ms after the "
                    "previous one, same column)"
                    % (cid, x, y,
                       (now - self.last_press["t_end"]) * 1000.0))
                return
            self.suppressed = False
            self.current = cid
            self.press_origin = (x, y)
            self.presses += 1
            self.out.press(x, y)
            log("press %d (contact id %d at %d,%d)"
                % (self.presses, cid, x, y))
        elif self.out.down:
            self.out.move(x, y)

    def tick(self, now):
        """Expire a press whose finger has gone and stopped reporting.

        The panel is change-driven, so after a finger lifts there may be no
        further reports to trigger the release.
        """
        if self.current is not None and self.last_real is not None and \
                (now - self.last_real) >= self.grace_secs:
            debug("  tick: id 1 absent for %dms -> release"
                  % int((now - self.last_real) * 1000))
            self._release()


# ---------------------------------------------------------------------------
# Device discovery and acquisition
# ---------------------------------------------------------------------------
def _read_hex(path, name):
    try:
        with open(os.path.join(path, name)) as f:
            return int(f.read().strip(), 16)
    except (OSError, ValueError):
        return None


def _has_abs(path, code):
    try:
        with open(os.path.join(path, "capabilities", "abs")) as f:
            mask = int(f.read().strip(), 16)
    except (OSError, ValueError):
        return False
    return bool(mask >> code & 1)


def _uevent(device):
    try:
        with open(os.path.join(device, "uevent")) as f:
            return f.read()
    except OSError:
        return ""


def find_hidraw():
    """The hidraw node behind the multitouch interface.

    Selected by USB identity and interface number, not by the HID instance
    name: that name is renumbered on every re-enumeration, so pinning it makes
    the driver wait forever after a USB reset, suspend or replug.
    """
    if SRC_HID:
        return SRC_HID
    for entry in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        device = os.path.join(entry, "device")
        text = _uevent(device)
        if ("HID_ID=" + HID_ID) not in text:
            continue
        phys = ""
        for line in text.splitlines():
            if line.startswith("HID_PHYS="):
                phys = line[len("HID_PHYS="):].strip()
        if not phys.endswith(HID_PHYS_SUFFIX):
            continue
        return "/dev/" + os.path.basename(entry)
    return None


def find_raw_event():
    """The raw multitouch evdev node, selected by capability not by name.

    Several interfaces are all called "TOUCH TOUCH"; only the touchscreen
    advertises ABS_MT_SLOT.  Virtual devices are skipped because this driver's
    own uinput node matches the same vendor/product.
    """
    if SRC_EVDEV and os.path.exists(SRC_EVDEV):
        return SRC_EVDEV
    for path in sorted(glob.glob("/sys/class/input/event*/device")):
        if "/virtual/" in os.path.realpath(path):
            continue
        if _read_hex(path, "id/vendor") != VENDOR:
            continue
        if _read_hex(path, "id/product") != PRODUCT:
            continue
        if not _has_abs(path, e.ABS_MT_SLOT):
            continue
        return os.path.join("/dev/input",
                            os.path.basename(os.path.dirname(path)))
    return None


class RawGrab:
    """Hold the raw evdev node shut so X cannot read the phantoms itself."""

    def __init__(self):
        self.dev = None

    def ensure(self):
        if self.dev is not None:
            return
        while _RUNNING:
            dev = None
            try:
                path = find_raw_event()
                if not path:
                    log("waiting for the raw touchscreen to appear")
                    time.sleep(1)
                    continue
                dev = InputDevice(path)
                dev.grab()
                self.dev = dev
                log("grabbed %s (held shut, not read)" % path)
                return
            except OSError as exc:
                if dev is not None:
                    try:
                        dev.close()
                    except OSError:
                        pass
                log("waiting to grab the raw touchscreen: %r" % (exc,))
                time.sleep(1)

    def reset(self):
        if self.dev is None:
            return
        try:
            self.dev.ungrab()
        except OSError:
            pass
        try:
            self.dev.close()
        except OSError:
            pass
        self.dev = None

    def close(self):
        self.reset()


def open_hid():
    while _RUNNING:
        path = find_hidraw()
        if not path:
            log("waiting for the raw HID node to appear")
            time.sleep(1)
            continue
        try:
            fd = os.open(path, os.O_RDONLY)
            log("reading raw reports from %s" % path)
            return fd
        except OSError as exc:
            log("waiting for %s: %r" % (path, exc))
            time.sleep(1)
    return -1


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def _stop(signum, frame):
    global _RUNNING
    log("signal %d, exiting" % signum)
    _RUNNING = False


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    out = Emitter()
    log("virtual device: %s (%s)" % (out.ui.device.path, NAME))
    grab = RawGrab()
    grab.ensure()
    hid = open_hid()
    if hid < 0:
        out.close()
        grab.close()
        return 0
    panel = Panel(out)

    log("policy: the real finger is HID contact id %d; anything else is a "
        "phantom. A press is released when it has been absent for %dms."
        % (PRIMARY_ID, int(GRACE_MS)))
    if panel.guard:
        log("ghost guard: on - a press starting within %dms of the last report "
            "of the previous press, within %d raw units in X but %d or more in "
            "Y, is dropped (TOUCH_GHOST_GUARD=0 disables)"
            % (int(panel.guard_gap_ms), panel.guard_dx, panel.guard_dy))
    else:
        log("ghost guard: off")
    seen_lengths = set()
    late_check = time.monotonic() + 1.0

    try:
        while _RUNNING:
            try:
                ready, _, _ = select.select([hid], [], [], TICK_SECS)
                now = time.monotonic()
                if late_check is not None and now >= late_check:
                    # Xorg has opened the node by now and may have reset the
                    # absinfo.  Record what it actually advertises.
                    out.log_absinfo("after Xorg opens")
                    late_check = None
                if not ready:
                    panel.tick(now)
                    continue
                data = os.read(hid, 64)
                if not data:
                    raise OSError(errno.ENODEV, "empty read")
                if data[0] != REPORT_ID:
                    continue
                payload = data[1:]
                if len(payload) not in seen_lengths:
                    seen_lengths.add(len(payload))
                    note = "" if len(payload) == REPORT_LEN else \
                        " (descriptor implies %d)" % REPORT_LEN
                    log("report 0x%02X payload length %d%s"
                        % (data[0], len(payload), note))
                contacts, count = decode(payload)
                debug("  report: %d tipped, payload %d, count=%s, contacts=%s"
                      % (len(contacts), len(payload), count, contacts))
                panel.feed(contacts, now)
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                log("read error: %r; reconnecting" % (exc,))
                out.release()
                panel.current = None
                panel.last_real = None
                try:
                    os.close(hid)
                except OSError:
                    pass
                # The whole USB device may have re-enumerated, taking the
                # grab with it.
                grab.reset()
                grab.ensure()
                hid = open_hid()
    finally:
        out.close()
        grab.close()
        try:
            if hid >= 0:
                os.close(hid)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
