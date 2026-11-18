# Getting a firmware image (or the protocol) for 27c6:8191

The latch described in [`FIRMWARE-LATCH.md`](FIRMWARE-LATCH.md) cannot be fixed
above the kernel: the panel keeps Tip Switch set on a slot it has stopped
updating, so the report stream contains no event meaning "up". The repair has to
be a different firmware, or a firmware operating path that does not run the
failing slot allocator.

There is no image to analyse and no public protocol to use. This file is the
two things that change that: a request to send, and a procedure for capturing
the vendor's own tool so the protocol is observed rather than guessed.

## 1. The facts to put in the request

Everything below is read from the device itself.

| Item | Value |
|---|---|
| USB identity | `27c6:8191`, `bcdDevice 1.00`, manufacturer "TOUCH", product "TOUCH", no serial |
| Interfaces | 0: digitizer + keyboard, 946-byte descriptor, ten-point report `0x0D`; 1: vendor page `0xFF00`, report 1 in / 2 out, 63-byte payloads; 2: mouse fallback |
| HID features | `0x21` Input Mode / Device Index (0–10), `0x0A` Contact Count Maximum = 10 |
| Report rate | ~96 Hz while touched, silent when idle |
| Link | Full Speed 12 Mbit/s, bus powered, 200 mA, behind a hub |
| EDID | PnP `EMO`, product code `0x0001`, EDID 1.4, week 33/2022, 34×20 cm, name "Monitor", serial string "demoset-1" |
| Mode | 3840×2400 @ 60 Hz, 344×196 mm active area |

The EDID says white-label demo module, not a retail product, and no bus string
identifies the brand. That means the request has to go to whoever supplied the
monitor, with Goodix cc'd if that route exists.

## 2. What to ask for, in priority order

1. **The firmware image for this module and the tool that flashes it.** State
   the PID, the EDID string `demoset-1`, and the week/year.
2. **The interface-1 command set** (or the updater's source), if they will not
   hand over a binary. That is enough to read the flash out ourselves — the
   Goodix family updater defines `Read(addr, buf, len)` alongside
   `Write(...)`, so a read path exists in the architecture even though this
   panel's model is not in the public tool.
3. **The panel part number and whether a later firmware revision fixes stuck
   contacts**, so a replacement module can be matched if no image appears.
4. **An RMA or replacement** if none of the above is available. A module that
   latches a contact for four seconds after a tap is defective, not quirky.

Say plainly that the host is Linux 6.12 with stock `hid-multitouch`, that no
vendor driver is in use, and that the request is for the module's firmware, not
for a Linux driver.

## 3. Describe the defect in their terms

Keep it concrete and measurable; it reads as a bug report rather than a
complaint:

- after a single finger tap the controller keeps one contact asserted for
  3.0 s and 4.1 s in two captured cases, with Tip Switch held at 1;
- the spurious contacts carry the correct X and an invented Y, which pins to
  the logical limits `0`/`1` and `9599` (`0x257F`) far more often than chance;
- Contact Count never falls below the number of tipped blocks and exceeds it by
  one in ~12% of reports, so the tracker and the frame disagree;
- Scan Time is a constant `0x0101` in all 1587 captured reports.

Offer the raw captures. They are small and they make the report unarguable.

## 4. If they send a tool: capture it, do not guess

A 64-byte vendor channel with an unknown command set is exactly the thing not
to fuzz: a wrong frame can leave the panel unusable, and a wrong write can brick
it. One captured session removes the guesswork and usually yields the image as
a side effect, because updaters read flash to verify before they write.

Procedure:

1. Get the vendor's Windows tool and its bundled firmware, or their Linux build.
2. Run it against the module on a machine you can afford to disturb, with
   packet capture already running. On Windows that is Wireshark with USBPcap;
   on Linux it is `usbmon` (`modprobe usbmon`, capture `usbmon3`, filter on
   `27c6`).
3. Let the tool do a *verify* or *read* operation first if it offers one. That
   single operation contains the read framing, the address layout and any
   checksum, with no write risk.
4. Save the capture as `.pcapng`. The parts that matter are every
   `SET_REPORT`/interrupt-OUT frame of 64 bytes, the responses on endpoint
   `0x82`, and the timing between them.
5. Only then write a reader against the observed framing.

If they will only give the Windows tool and no read-only mode, capture the
update anyway: the write sequence implies the read sequence, and the tool
almost always reads a version or a checksum block before flashing.

## 5. What happens with an image in hand

The analysis is ordinary binary work, and the target inside the image is
narrow:

- the routine that fills report `0x0D` — five-byte blocks, the constant
  `0x0101` Scan Time, the count byte;
- the slot-clear path taken when a contact is released, which is where the
  latch lives;
- the peak-detection or baseline code that produces the column-locked ghosts.

With those located, a corrected image is a build problem for the vendor, not
for us. Without an image, the two cheap device-level experiments in
[`FIRMWARE-LATCH.md`](FIRMWARE-LATCH.md) §8 — input mode, and noise/power/
grounding — are the only remaining probes that do not involve guessing.

## 6. Safety notes

- Do not flash the kiosk's only panel with an unverified image. If a second
  identical module can be bought cheaply, flash that first.
- Keep `probes/probe-hidraw.py` output of a known-good gesture set before and
  after any change; the count laws and the column-lock statistic are the
  comparison.
- A USB reset recovers a mode change or a wedged controller; it does not
  recover a bad flash.
