#!/bin/sh
# Ask the controller to track fewer contacts, capture, then restore.
#
#   contact-max-trial.sh [MAX] [SECONDS] [NO_DATA_ABORT_S]
#
# MAX defaults to 1.  The point is to remove the slot allocator rather than
# filter its output: if the firmware honours Contact Count Maximum, report 0x0D
# cannot contain a second contact, so the column-locked phantoms have nowhere
# to live.  Contact Count Maximum is restored to 10 on every exit path.
set -u

MAX="${1:-1}"
SECS="${2:-120}"
ABORT_AFTER="${3:-40}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE=/tmp/goodix-probe.jsonl

restore() {
    echo "restoring contact maximum = 10"
    python3 "$HERE/probe-contact-max.py" /dev/hidraw0 10 ||
        echo "RESTORE FAILED - replug the panel"
}
trap restore EXIT INT TERM

echo "setting contact maximum = $MAX"
python3 "$HERE/probe-contact-max.py" /dev/hidraw0 "$MAX" --keep || exit 1

echo "capturing ${SECS}s -> $PROBE"
python3 "$HERE/probe-hidraw.py" "$SECS" &
CAP=$!
sleep "$ABORT_AFTER"

if [ ! -s "$PROBE" ]; then
    echo "no reports after ${ABORT_AFTER}s - panel silent with contact max $MAX"
    kill "$CAP" 2>/dev/null
    exit 1
fi

wait "$CAP"
echo "capture finished"
