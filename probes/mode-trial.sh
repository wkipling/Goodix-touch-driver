#!/bin/sh
# Run one InputMode trial: select a mode, capture the raw report stream for a
# fixed window, then restore the mode the stock driver uses.
#
#   mode-trial.sh [MODE] [SECONDS] [NO_DATA_ABORT_S]
#
# MODE defaults to 1 (single touch).  InputMode 2 (multitouch) is restored on
# every exit path, including a watchdog abort, so the panel cannot be left in a
# mode nobody expects.  See docs/FIRMWARE-LATCH.md section 8.
#
# The watchdog exists because a wrong mode can leave the digitizer silent.  It
# aborts and restores rather than waiting out the whole window; the caller must
# touch the panel within NO_DATA_ABORT_S of the start.
set -u

MODE="${1:-1}"
SECS="${2:-180}"
ABORT_AFTER="${3:-40}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE=/tmp/goodix-probe.jsonl

restore() {
    echo "restoring InputMode=2"
    python3 "$HERE/probe-input-mode.py" /dev/hidraw0 2 ||
        echo "RESTORE FAILED - reset the panel, see probe-input-mode.py"
}
trap restore EXIT INT TERM

echo "setting InputMode=$MODE"
python3 "$HERE/probe-input-mode.py" /dev/hidraw0 "$MODE" --keep || exit 1

echo "capturing ${SECS}s -> $PROBE"
python3 "$HERE/probe-hidraw.py" "$SECS" &
CAP=$!
sleep "$ABORT_AFTER"

if [ ! -s "$PROBE" ]; then
    echo "no reports after ${ABORT_AFTER}s - panel silent in mode $MODE"
    kill "$CAP" 2>/dev/null
    exit 1
fi

wait "$CAP"
echo "capture finished"
