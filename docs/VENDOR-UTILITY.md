# The vendor's own tools

Found 2026-11-18 from `https://www.ehomewei.com/pages/download`. Three
downloads, all public, no account required:

| file | size | what it is |
|---|---|---|
| `EHome_Wei_Setup.exe` | 15.7 MB | Windows app (`DisplayController.exe` 1.0.0.135, MFC) |
| `Ehomewei_Display1.1.7_261104.dmg` | 18.8 MB | macOS build of the same |
| `ISP.zip` | 2.1 MB | "USB firmware programmer" |

Direct URLs (Shopify CDN, `?v=` is the file revision):

```
https://cdn.shopify.com/s/files/1/0694/9892/9364/files/EHome_Wei_Setup.exe?v=1758880108
https://cdn.shopify.com/s/files/1/0694/9892/9364/files/Ehomewei_Display1.1.7_261104.dmg?v=1793774046
https://cdn.shopify.com/s/files/1/0694/9892/9364/files/ISP.zip?v=1765593553
```

## `ISP.zip` is not the touch tool

Contents:

```
ISP/CH343PT.DLL        WCH CH343 USB-serial
ISP/CH375DLL.dll       WCH CH375 USB host
ISP/WCH55xISPDLL.dll   WCH 8051 (CH55x) in-system programmer
ISP/ISP.exe
ISP/language.ini
ISP/firmware/          EMPTY
```

It is a WCH 8051 programmer — the scaler/board side, not the Goodix GT-class
touch controller. It needs a physical programmer (CH343 serial or CH375) and a
firmware image, and the `firmware/` directory ships empty. **Not usable over
the USB-C connection, and there is nothing here to flash.**

The empty `firmware/` directory is still informative: firmware images are
distributed separately, presumably on request. That supports asking support
rather than hunting for a public image.

## The Windows app does speak HID to the monitor

Static inspection of `DisplayController.exe`
(sha256 `5089d641b3d777a29a9fd9896adfc54b3699ccab5746834ce2f7adad5191b7f4`):

- imports **`HidD_GetFeature`, `HidD_SetFeature`, `HidD_SetOutputReport`**
- classes: `TouchInputManager`, `DeviceCommunicatorProxy`, `HidDeviceInfo`,
  `DeviceReport`, and a listener taking `OnReceivedDeviceMessage(..., HidDeviceInfo, DeviceReport)`
- strings: `stylus`, `Stylus set`, `Touch supported`, `supportTouch %d`,
  `open_device`, `write open divice false, %s`
- also references `SOFTWARE\Microsoft\Wisp\Pen\Digimon` — the Windows pen/touch
  behaviour key

So the utility genuinely writes to HID devices **and** touches a Windows
registry key. Which of the two implements the "touch / stylus switch" is not yet
established, and that matters: a registry write would be irrelevant on Linux,
while an output/feature report would be exactly the vendor command we want.

The imports are consistent with this panel's channels: feature reports for
InputMode on interface 0, and an output-report path matching interface 1's
vendor collection (report 2).

No model codes (`RQHG-…`, `EHW01…`) appear anywhere in the installer or the
extracted tree, so the supported-model list is server-side or UI-supplied. That
is consistent with the R13 Pro not being listed.

## Who to address the request to

The executable's version resource names three entities, and they are not the
same company:

| Field | Value |
|---|---|
| `FileVersion` / `ProductVersion` | `1.0.0.135` |
| `ProductName` | `EHOMEWEI` |
| `LegalCopyright`, one language block | `Copyright 2025 ShenZhen EHomeWei Electronics Co.Ltd` |
| `LegalCopyright`, another block | `2025 LANMEY JAPAN` |
| string-table translation | `0x0412` (Japanese), UTF-16 |

The store page is operated by **HK LANMEY INDUSTRIES LIMITED** (Kowloon, Hong
Kong). So the chain runs Hong Kong retail, a Japanese branding arm, and
Shenzhen engineering. A firmware request belongs with the Shenzhen entity or
with the supplier named on the invoice; the Shopify support inbox sits furthest
from the module. `support@ehomewei.com` is still the right first contact — send
it there, but write it so it can be forwarded to engineering without a
follow-up question.

One thing the version resource does not give: any touch firmware revision. The
build number above is the *Windows app's*. Nothing in the device strings, the
HID descriptor or the reports exposes which firmware the panel itself runs, so
there is currently no way to say whether this module is on a current build.
That is worth asking for explicitly — it is the cheapest question in the list
and the one that decides whether a reflash is even a change.

## Re-checked 2026-11-18

The download page still offers exactly these three files and nothing else. There
is no touch-specific utility, no touch firmware image, and no changelog. The
Windows app is `1.0.0.135`, extracted binary
`DisplayController.exe` sha256
`5089d641b3d777a29a9fd9896adfc54b3699ccab5746834ce2f7adad5191b7f4`,
which matches the copy analysed above.

## What is blocked, and the two ways round it

Full decompilation was attempted with Ghidra 12.1.4 and abandoned: import plus
auto-analysis of the 2.6 MB MFC binary exceeded the provider's 330 s deadline,
and after raising it to 20 min the MCP client's own tool deadline expired
instead. Neither is a property of the binary; a longer client deadline or the
CLI would work.

Before spending more on static work, the cheaper decisive experiment is
**runtime**:

1. Run `EHome_Wei_Setup.exe` on a Windows machine (or under Wine) with the panel
   attached, and capture `usbmon` while toggling the touch/stylus switch. If the
   toggle produces a HID output or feature report, that *is* the vendor command
   and no decompilation is needed. Risk: the app ships `WebView2Loader.dll`, so
   it may require the Edge WebView2 runtime and refuse to start under Wine.
2. Otherwise, decompile with a client deadline that allows it, and look for the
   `supportTouch` format string's callers.

Either way the ask to EHOMEWEI is now specific enough to be worth sending:
R13 Pro, touch/stylus switch, and whether a touch firmware image exists.
