# Goodix touch-panel packages in the Microsoft Update Catalog

Written 2026-11-18. This corrects an earlier conclusion: the public update
search in [`FIRMWARE-LATCH.md`](FIRMWARE-LATCH.md) said there is no public
firmware for this panel. That was based on looking for the hardware ID and for
the Linux updater, and it missed the catalog's Goodix *touch panel* packages,
which are neither.

## How to get them

```
python3 probes/fetch-catalog-goodix.py "Goodix HIDClass"
7z x -o/tmp/goodix-pkgs/x /tmp/goodix-pkgs/<name>.cab
```

The catalog needs a browser user agent, keys its results by GUID, and only
reveals the download link after a form POST to `DownloadDialog.aspx`. The script
handles all three. Note the INFs are **UTF-16**, so `grep` without conversion
misses everything in them — that is what hid this file from the first pass.

## What the packages contain

Nine distinct payloads came back, of three kinds:

| Kind | Contents |
|---|---|
| Wake-on-touch policy | `GoodixTouchScreen.inf` + `GoodixWOTService.exe`, targets `ACPI\GXTP6936` |
| I2C HID panel driver | `gt_berlinb_thp_driver.inf`, targets `ACPI\GXTS7986` |
| **Firmware update driver** | `tpupdate_driver.inf` + `tpupdate_driver.dll` + **`tpfw.bin`** + `tpcfgsidN.cfg` |

The third kind is the interesting one. `tpupdate_driver.inf` is a UMDF filter
driver that installs itself on a HID collection, reads the current firmware
version, and flashes `tpfw.bin`. Its registry settings name the chip families it
can drive:

```
HKR,"tpupdate_7868q_driver","ICSelect",0x00010001,1
; 1 for 7863, 2 for 7868Q, 3 for berlinB
```

## One package matches this panel

Package `ee49202f-1ae9-42d8-b3b5-13c7658d565f`, driver version 0.7.4.1
(2023-02-17), declares:

```
%DeviceName%=MyDevice_Install,HID\VID_27C6&UP:FF00_U:0001
```

`UP:FF00_U:0001` is exactly this panel's vendor collection — usage page `0xFF00`,
usage `0x0001`, the interface carrying reports 1 (in) and 2 (out). Its settings
are `ICSelect=1` and `FirmwareFlag=0x0C`, and it ships:

| File | Size | SHA-256 |
|---|---|---|
| `tpfw.bin` | 86,272 | `babbe3db1a0e240b26b7414c84261817210a36b7103d39e9bc9c787b38db0c02` |
| `tpupdate_driver.dll` | 113,784 | `b3ba04ac5917c1832a797e7ccdcb32584bea8b91e22e57f716e893b17cce3f92` |
| `tpcfgsid2.cfg` | 2,760 | `f5f1ffe5485ac9f1bfc263f50ffc3d4817377be3efabbbd0e790284bbdeb0bec` |

Three things follow.

1. **The chip family is almost certainly 7863.** The INF's own comment maps
   `ICSelect=1` to 7863, and this is the package that matches our collection.
2. **The protocol is no longer a guess.** `tpupdate_driver.dll` is Goodix's own
   implementation of read and write commands over HID reports for that chip.
   Its strings include `hidTarget_InputReportGet`, `hidTarget_OutputReportSet`,
   `send read report cmd`, `read report status failed` and
   `checksum is invalid:0x%02x`, so it contains the framing, the command codes
   and the checksum. That is the thing previously said to require a Windows
   capture to obtain.
3. **Do not flash the bundled `tpfw.bin`.** It is one OEM panel's image within
   the 7863 family, with that panel's geometry and config. The INF matches on
   vendor usage page alone, not on product id, so it will happily offer to flash
   a panel it was not built for.

## The matched driver cannot talk to this panel

Analysed 2026-11-18. `tpupdate_driver.dll` imports exactly one way to send
anything to the device, and it is feature reports:

| DLL | Function | Role |
|---|---|---|
| `HID.DLL` | `HidD_GetHidGuid`, `SetupDi*` (SETUPAPI) | enumerate HID collections |
| `HID.DLL` | `HidP_GetCaps` | read the collection's caps |
| `HID.DLL` | **`HidD_SetFeature`** | **the only command path** |
| `KERNEL32` | `CreateFileA/W`, `ReadFile` | open the collection, read input reports |

There is no `WriteFile` and no `HidD_SetOutputReport`, so every command it sends
is a feature report. Its strings describe that flow: `send read report cmd`,
`read report status failed`, `checksum is invalid:0x%02x`, and per-chip
`disable report` sequences.

Disassembly of the one `HidD_SetFeature` call site confirms the shape:

```asm
movslq %edx,%rdi          ; rdi = len
xor    %edx,%edx
lea    0x20(%rsp),%rcx
lea    0x41(%rdx),%r8d    ; memset(local, 0, 0x41)   -> 65-byte buffer
cmp    $0x41,%edi         ; len > 65 -> "Write len error. writelen = %d."
...
call   0x180008e5e        ; HidD_SetFeature(handle, buf, len)
```

A 65-byte feature report — report ID plus 64 bytes of payload, the same shape as
the Linux updater's `0x0e`/`0x0b` protocol. So the Windows package and the Linux
repository share a transport design, and both differ from this panel's
interrupt-report channel.

This panel's vendor collection declares no feature reports at all:

```
06 00 ff 09 01 a1 01 85 01 09 01 75 08 95 3f 81 02 85 02 09 ff 75 08 95 3f 91 02 c0
             ^input report 1 (63 bytes)      ^output report 2 (63 bytes)
```

One `0x81` item, one `0x91` item, and no `0xb1` feature item anywhere.

So the INF matches on usage page and usage only, which sibling panels share,
and the payload plus driver behind it cannot drive *this* collection. Three
consequences:

1. **Installing that package would not update this panel.** It would fail at
   `HidD_SetFeature`, which is why the match alone is not a fix.
2. **This panel's vendor channel is output-report based** — report 2 in, report
   1 out. That is why the earlier GTX5-framed attempt wrote to endpoint `0x02`:
   the right direction, wrong framing.
3. **The vendor question is now specific.** Not "is there an update" but: a
   7863-family module whose `FF00`/`0001` collection exposes only input report 1
   and output report 2, with no feature reports — which package and which
   command framing applies to it?

## Next step

Analyse `tpupdate_driver.dll` to recover the command framing, then use a
*read only* command against the panel. A version query identifies the chip and
gives a firmware version; if the flash-read path is present it gives a dump.
Writing anything before those are understood risks the panel.
