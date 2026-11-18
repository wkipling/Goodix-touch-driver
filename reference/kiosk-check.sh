#!/bin/bash
# Self-check for the kiosk fix-ups. Run after a reboot, or whenever the
# panel starts behaving oddly: every line says what it expects, so one read
# tells you which piece is missing.
#
# Usage:  kiosk-check          (on the kiosk itself, as the kiosk user)
#
# Needs no root for the process checks; the journal and device checks fall
# back gracefully when passwordless sudo is not available.

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/home/kiosk/.Xauthority}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"

echo "== panel mode (expect: 3840x2400 at 60, on DP-2) =="
xrandr --query 2>/dev/null | grep -E "connected primary|3840x2400"

echo
echo "== services (expect: all active) =="
for u in touch-fuzz touch-filter kiosk kiosk-touch-fix kiosk-noscroll kiosk-hide-header; do
  printf '  %-20s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null)"
done

echo
echo "== panel noise floor (expect: fuzz=80 on all four axes) =="
if sudo -n true 2>/dev/null; then
  sudo -n journalctl -u touch-fuzz.service -n 2 --no-pager 2>/dev/null | tail -1 | sed 's/^/  /'
fi

echo
echo "== touch hardware (expect: raw 'TOUCH TOUCH' + 'TOUCH TOUCH Filtered') =="
for f in /sys/class/input/event*/device/name; do
  n=$(cat "$f" 2>/dev/null)
  case "$n" in
    *TOUCH*) printf '  %-24s %s\n' "$(basename "$(dirname "$(dirname "$f")")")" "$n" ;;
  esac
done

echo
echo "== touch filter (expect: grab refused to us, i.e. the filter holds it) =="
if sudo -n true 2>/dev/null; then
  sudo -n python3 - <<'PY'
import fcntl, errno, os, sys
EVIOCGRAB = 0x40044590
p = "/dev/input/by-id/usb-TOUCH_TOUCH-event-if00"
try:
    fd = os.open(p, os.O_RDONLY)
except OSError as e:
    print("  cannot open %s: %s" % (p, e)); sys.exit(0)
try:
    fcntl.ioctl(fd, EVIOCGRAB, 1)
    print("  NOT GRABBED - the filter is not holding the raw panel")
    fcntl.ioctl(fd, EVIOCGRAB, 0)
except OSError as e:
    print("  grabbed (%s) - phantoms cannot reach Chromium directly"
          % errno.errorcode.get(e.errno))
finally:
    os.close(fd)
PY
  sudo -n journalctl -u touch-filter.service -n 3 --no-pager 2>/dev/null | sed 's/^/  /'
else
  echo "  (needs sudo -n to test the grab and read the journal)"
fi

echo
echo "== slider injector (expect: last report shows notPatched [] and styleTag true) =="
if systemctl is-active --quiet kiosk-touch-fix 2>/dev/null; then
  sudo -n journalctl -u kiosk-touch-fix.service -n 1 --no-pager 2>/dev/null | sed 's/^/  /'
else
  echo "  kiosk-touch-fix NOT RUNNING - slider drags will scroll the page"
fi

echo
echo "== micro-scroll guard (expect: installed true, range 0 on a settled page) =="
sudo -n journalctl -u kiosk-noscroll.service -n 1 --no-pager 2>/dev/null | sed 's/^/  /'

echo
echo "== on-screen keyboard (expect: onboard running, hidden, auto-show false) =="
pgrep -af "onboar[d] --layout" | cut -c1-100 | sed 's/^/  /'
pgrep -af "kiosk-os[k].py" | cut -c1-100 | sed 's/^/  /'
if command -v xdotool >/dev/null 2>&1; then
  n=$(xdotool search --onlyvisible --class onboard 2>/dev/null | wc -l)
  echo "  visible keyboard windows: $n (0 = hidden, which is correct when idle)"
fi
gsettings get org.onboard.auto-show enabled 2>/dev/null | sed 's/^/  auto-show: /'
tail -2 /tmp/kiosk-osk.log 2>/dev/null | sed 's/^/  /'

echo
echo "== chromium (expect: kiosk + touch-events + debugging port) =="
ps -eo args | grep -m1 "[c]hromium .*--kiosk" | tr ' ' '\n' \
  | grep -E "^--(kiosk|disable-pinch)$|^--(touch-events|remote-debugging-port|force-device-scale-factor)=" \
  | sed 's/^/  /'

echo
echo "== last contact seen by the filter =="
sudo -n journalctl -u touch-filter.service --no-pager 2>/dev/null \
  | grep -E "contact .* (forwarded|dropped)" | tail -1 | sed 's/^/  /'
