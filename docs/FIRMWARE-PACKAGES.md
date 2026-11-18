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

## Next step

Analyse `tpupdate_driver.dll` to recover the command framing, then use a
*read only* command against the panel. A version query identifies the chip and
gives a firmware version; if the flash-read path is present it gives a dump.
Writing anything before those are understood risks the panel.
