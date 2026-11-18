#!/bin/sh
# Run the suites against a driver implementation (default: ../src).
#
#     ./run.sh [path-to-goodix-touch-driver.py]
#
# These tests replace UInput and the clock, so they do not touch the panel.
# Requires python3-evdev, which is present on the kiosk.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
impl=${1:-"$here/../src/goodix-touch-driver.py"}

for t in test-touch-hid.py test-touch-release.py test-touch-driver.py; do
    printf '\n===== %s =====\n' "$t"
    ( cd "$here" && python3 "$t" "$impl" )
done

printf '\nall suites passed against %s\n' "$impl"
