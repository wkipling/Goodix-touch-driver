#!/usr/bin/env python3
"""Clean up the touch panel's stream: drop phantom extra contacts.

The panel (Goodix 27c6:8191) emits bursts of phantom contacts while a
finger is down - measured: 62 phantoms during one 16s test, each living
20-180ms, usually carrying the real finger's X with a garbage Y. Chromium
sees them as two/three-finger gestures, which cancels single-finger drags
mid-gesture and can turn taps into holds (a press the page never sees
resolve is concluded to be a hold).

This daemon grabs the raw device and re-emits the stream through a virtual
uinput touchscreen, with these rules:

- a contact born while no other contact is active ("alone") is trusted and
  forwarded immediately - real touches always start this way, and a tap
  has to feel instant
- a contact born while another is active is a candidate second finger
  (pinch/zoom). It is forwarded once it has stayed down for
  SECOND_TRUST_MS (~200ms). A ghost dies within 180ms and never survives
  that; a real second finger does, and appears once the delay is up.

Trust is re-evaluated on **every** tick, never decided once at birth, and
that is the whole point: a contact's verdict has to be able to change as
the world does. The previous version stamped each contact at birth and
counted frames, which failed two ways. The panel only emits frames when
the touch *changes*, so a still finger produced almost none and a real
second contact could be starved until the finger moved or lifted. And a
real tap that landed during a phantom storm was rejected permanently and
lost, because the count at birth was frozen into the verdict. Both are
time-based now, and a 20ms idle tick re-evaluates pending contacts even
while the panel is silent.

At most MAX_CONTACTS contacts are forwarded at once. Chromium runs with
--disable-pinch on this kiosk, so a third contact is noise by definition.

If this process exits or is killed, the grab is released and the raw
device works again (with the phantoms).

Requires root (grabs /dev/input, creates /dev/uinput).
"""

import os
import select
import signal
import sys
import time

from evdev import InputDevice, InputEvent, UInput, ecodes

SRC_PATH = os.environ.get(
    "TOUCH_FILTER_SOURCE",
    "/dev/input/by-id/usb-TOUCH_TOUCH-event-if00",
)
# How long a contact born while another was already active must stay down
# before it counts as a real second finger. Must exceed the longest
# phantom (~180ms), or ghosts get promoted to fingers.
SECOND_TRUST_MS = int(os.environ.get("TOUCH_FILTER_SECOND_TRUST_MS", "200"))
# Idle tick: how often to re-evaluate trust while the panel is silent.
TICK_MS = int(os.environ.get("TOUCH_FILTER_TICK_MS", "20"))
# How long a contact may sit completely still before it is assumed stuck.
# A phantom that never lifts would otherwise hold BTN_TOUCH down forever,
# which the page sees as a press that never ends - a hold. Motion resets
# this, so a slow slider drag is never cut short.
STUCK_MS = int(os.environ.get("TOUCH_FILTER_STUCK_MS", "5000"))
# Concurrent contacts to forward at most (pinch is disabled in Chromium).
MAX_CONTACTS = int(os.environ.get("TOUCH_FILTER_MAX_CONTACTS", "2"))
# Log every raw frame and every emitted contact. Off by default: at panel
# frame rates this is a lot of journal, but it is the only way to compare
# what the panel sends against what we forward.
DEBUG = os.environ.get("TOUCH_FILTER_DEBUG", "") not in ("", "0")
NAME = "TOUCH TOUCH Filtered"

EV_ABS, EV_KEY, EV_SYN = ecodes.EV_ABS, ecodes.EV_KEY, ecodes.EV_SYN
SLOT = ecodes.ABS_MT_SLOT
TID = ecodes.ABS_MT_TRACKING_ID
POS_X = ecodes.ABS_MT_POSITION_X
POS_Y = ecodes.ABS_MT_POSITION_Y
BTN_TOUCH = ecodes.BTN_TOUCH
ABS_X, ABS_Y = ecodes.ABS_X, ecodes.ABS_Y
SYN_REPORT = ecodes.SYN_REPORT


def log(msg):
    print(time.strftime("%F %T"), msg, flush=True)


def ev(typ, code, value):
    return InputEvent(0, 0, typ, code, value)


def emit(ui, events):
    for e in events:
        ui.write_event(e)


class Filter:
    def __init__(self, ui, trust_secs, max_contacts, stuck_secs=5.0):
        self.ui = ui
        self.trust_secs = trust_secs
        self.max_contacts = max_contacts
        self.stuck_secs = stuck_secs
        self.reset()

    def reset(self):
        self.cur_slot = 0
        self.active = {}      # slot -> tracking id
        self.meta = {}        # tid -> {"t0": monotonic, "others": n, "x":, "y":}
        self.emitted = {}     # slot -> tid currently reported to the virtual device
        self.sent_pos = {}    # tid -> (x, y) last written for that contact
        self.primary = None   # tid whose position BTN_TOUCH/ABS_X/ABS_Y describe
        self.primary_pos = None
        self.dropped = 0
        self.forwarded = 0
        self.stuck = 0
        self.cohort = 0

    def _release_output(self):
        out = []
        for slot, tid in self.emitted.items():
            out += [ev(EV_ABS, SLOT, slot), ev(EV_ABS, TID, -1)]
        if self.emitted or self.primary is not None:
            out.append(ev(EV_KEY, BTN_TOUCH, 0))
        self.emitted = {}
        self.sent_pos = {}
        self.primary = None
        self.primary_pos = None
        return out

    def _trusted(self, info, now):
        # Inside a storm cohort only the highest slot is trusted, and it is
        # trusted at once so a tap stays instant.
        if info["conflicted"]:
            return info["designated"]
        # Born with nothing else live: a real gesture started.
        if info["others"] == 0:
            return True
        # Born while others were active: only age separates finger from ghost.
        return (now - info["t0"]) >= self.trust_secs

    def process(self, evs):
        """Fold one frame in, then re-evaluate every contact.

        An empty list is a valid call - it is the idle tick, and it is what
        lets a deadline fire while the panel is emitting nothing.
        """
        now = time.monotonic()
        # Contacts born in one call arrived in one HID report, i.e. at the same
        # instant. They are a cohort, and a cohort is where phantoms hide.
        self.cohort += 1
        born = []

        for typ, code, val in evs:
            if DEBUG:
                name = ecodes.bytype.get(typ, {}).get(code, code)
                log("  RAW %-18s %s" % (name, val))
            if typ == EV_ABS and code == SLOT:
                self.cur_slot = val
            elif typ == EV_ABS and code == TID:
                if val == -1:
                    tid = self.active.pop(self.cur_slot, None)
                    if tid is not None:
                        self.meta.pop(tid, None)
                        self.sent_pos.pop(tid, None)
                        if tid not in self.emitted.values():
                            self.dropped += 1
                            log("contact %d dropped (never trusted; %d phantoms so far)"
                                % (tid, self.dropped))
                else:
                    self.meta[val] = {
                        "t0": now, "others": len(self.active),
                        "x": None, "y": None, "moved": now,
                        "cohort": self.cohort, "slot": self.cur_slot,
                        "conflicted": False, "designated": False,
                    }
                    self.active[self.cur_slot] = val
                    born.append(val)
            elif typ == EV_ABS and code in (POS_X, POS_Y):
                tid = self.active.get(self.cur_slot)
                info = self.meta.get(tid) if tid is not None else None
                if info is not None:
                    if code == POS_X:
                        if info["x"] != val:
                            info["moved"] = now
                        info["x"] = val
                    else:
                        if info["y"] != val:
                            info["moved"] = now
                        info["y"] = val

        # A cohort of two or more is not two fingers - it is the panel's
        # phantom storm. Measured against the panel's own HID reports: in 216
        # of 223 storm frames the real finger was the LAST block, with the
        # phantoms ahead of it, so the real contact takes the highest slot.
        # Forward that one and only that one, immediately: no delay, and no
        # chance of the phantom being trusted just because it arrived first.
        # (A genuine two-finger gesture would lose its second finger here, but
        # Chromium runs with --disable-pinch on this kiosk.)
        if len(born) > 1:
            for tid in born:
                self.meta[tid]["conflicted"] = True
            winner = max(born, key=lambda t: self.meta[t]["slot"])
            self.meta[winner]["designated"] = True
            log("cohort of %d contacts: slots %s -> trusting slot %d only"
                % (len(born), sorted(self.meta[t]["slot"] for t in born),
                   self.meta[winner]["slot"]))

        # A contact that has stopped moving for a long time is stuck, not held.
        # Left alone it would pin BTN_TOUCH down and the page would call the
        # press a hold. Motion above keeps a slow drag alive.
        for slot, tid in list(self.active.items()):
            info = self.meta.get(tid)
            if info and (now - info["moved"]) > self.stuck_secs:
                log("contact %d force-released (frozen for %dms)"
                    % (tid, int((now - info["moved"]) * 1000)))
                self.meta.pop(tid, None)
                self.active.pop(slot, None)
                self.sent_pos.pop(tid, None)
                self.stuck += 1

        trusted = []
        for slot, tid in self.active.items():
            info = self.meta.get(tid)
            if info and self._trusted(info, now):
                trusted.append((info["t0"], slot, tid))
        trusted.sort()
        if len(trusted) > self.max_contacts:
            trusted = trusted[:self.max_contacts]

        out = []
        keep = {tid for (_, _, tid) in trusted}

        # No trusted contact left means the gesture is over. Anything still
        # "down" on the panel at this point arrived during it and never
        # qualified - debris, not a new touch. Drop it now, because a contact
        # kept here would be promoted by its age a moment later and reach the
        # page as a press that begins after the finger left and never ends:
        # which reads as a hold.
        if not trusted:
            for slot, tid in list(self.active.items()):
                if tid not in self.emitted.values():
                    self.meta.pop(tid, None)
                    self.active.pop(slot, None)
                    self.dropped += 1

        # Release contacts we were forwarding that are no longer trusted.
        for slot, tid in list(self.emitted.items()):
            if tid not in keep:
                out += [ev(EV_ABS, SLOT, slot), ev(EV_ABS, TID, -1)]
                del self.emitted[slot]

        # Forward trusted contacts.
        for _, slot, tid in trusted:
            info = self.meta[tid]
            # Drop stale entries for this tid in other slots (slot reuse).
            for other_slot, other_tid in list(self.emitted.items()):
                if other_tid == tid and other_slot != slot:
                    out += [ev(EV_ABS, SLOT, other_slot), ev(EV_ABS, TID, -1)]
                    del self.emitted[other_slot]
            if self.emitted.get(slot) != tid:
                out += [ev(EV_ABS, SLOT, slot), ev(EV_ABS, TID, tid)]
                self.emitted[slot] = tid
                self.forwarded += 1
                log("contact %d forwarded (slot %d, born with %d active, %dms old, %d phantoms dropped)"
                    % (tid, slot, info["others"],
                       int((now - info["t0"]) * 1000), self.dropped))
            # Position passes through as reported. Jitter is handled where it
            # belongs - see the fuzz on the panel's axes, set via udev - so
            # there is nothing to smooth here. The idle tick re-evaluates every
            # contact, so the write stays conditional or it would spray motion
            # at 50Hz.
            x, y = info.get("x"), info.get("y")
            lx, ly = self.sent_pos.get(tid, (None, None))
            if x is not None and x != lx:
                out += [ev(EV_ABS, SLOT, slot), ev(EV_ABS, POS_X, x)]
                lx = x
            if y is not None and y != ly:
                out += [ev(EV_ABS, SLOT, slot), ev(EV_ABS, POS_Y, y)]
                ly = y
            if lx is not None or ly is not None:
                self.sent_pos[tid] = (lx, ly)

        # BTN_TOUCH and ABS_X/ABS_Y describe the oldest trusted contact.
        primary = trusted[0][2] if trusted else None
        if primary is not None:
            pinfo = self.meta[primary]
            pos = (pinfo.get("x"), pinfo.get("y"))
            if None not in pos and (primary != self.primary or pos != self.primary_pos):
                out += [ev(EV_KEY, BTN_TOUCH, 1),
                        ev(EV_ABS, ABS_X, pos[0]),
                        ev(EV_ABS, ABS_Y, pos[1])]
                self.primary, self.primary_pos = primary, pos
        elif self.primary is not None:
            out.append(ev(EV_KEY, BTN_TOUCH, 0))
            self.primary, self.primary_pos = None, None

        if out:
            out.append(ev(EV_SYN, SYN_REPORT, 0))
            emit(self.ui, out)

    def close(self):
        out = self._release_output()
        if out:
            out.append(ev(EV_SYN, SYN_REPORT, 0))
            emit(self.ui, out)


def open_source():
    while True:
        try:
            dev = InputDevice(SRC_PATH)
            log("source opened: %s (%s)" % (SRC_PATH, dev.name))
            return dev
        except OSError as exc:
            log("waiting for source: %r" % (exc,))
            time.sleep(3)


def clean_caps(caps):
    """Keep only the event types uinput needs and can enable."""
    return {et: codes for et, codes in caps.items()
            if et in (ecodes.EV_ABS, ecodes.EV_KEY)}


def main():
    source = open_source()
    caps = clean_caps(source.capabilities(absinfo=True))
    props = None
    try:
        props = list(source.input_props())
    except Exception:
        pass
    ui = UInput(
        events=caps,
        name=NAME,
        vendor=source.info.vendor,
        product=source.info.product,
        version=source.info.version,
        bustype=source.info.bustype,
        input_props=props,
    )
    log("virtual device: %s (%s)" % (ui.device.path, NAME))

    filt = Filter(ui, SECOND_TRUST_MS / 1000.0, MAX_CONTACTS, STUCK_MS / 1000.0)

    log("policy: second contact trusted after %dms, storm cohorts trust the "
        "highest slot only, %dms idle tick, %dms frozen-contact timeout"
        % (SECOND_TRUST_MS, TICK_MS, STUCK_MS))

    def shutdown(signum, frame):
        log("signal %d, exiting" % signum)
        try:
            filt.close()
        finally:
            source.close()
            sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    tick = TICK_MS / 1000.0
    while True:
        try:
            source.grab()
            log("grabbed %s; filtering" % SRC_PATH)
            pending = []      # events of the frame being assembled
            while True:
                ready, _, _ = select.select([source], [], [], tick)
                if not ready:
                    # Panel is quiet, but time still passes: anything whose
                    # deadline came due gets promoted (or expires) here.
                    filt.process([])
                    continue
                for event in source.read():
                    if event.type == EV_SYN and event.code in (SYN_REPORT, ecodes.SYN_DROPPED):
                        if pending:
                            filt.process(pending)
                        pending = []
                    else:
                        pending.append((event.type, event.code, event.value))
        except (OSError, IOError) as exc:
            log("source lost: %r" % (exc,))
            # Release anything we might be holding, then retry.
            try:
                filt.close()
            except Exception:
                pass
            filt.reset()
            try:
                source.close()
            except Exception:
                pass
            source = open_source()


if __name__ == "__main__":
    main()
