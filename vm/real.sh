#!/bin/bash
# make check-real / check-daemon-real / record-p2 / record-p3 / record-p3a / record-p3b / record-p3c / record-p5 / record-p5-stf / record-netem: restore trex_ready, step the guest clock, run the real-tier tests (or a recorder), always shut down.
# TREX_VM_RECORD=p2|p3|p3a|p3b|p3b-v6|p3c|p5|p5-stf|netem picks the recorder (vm/record_<x>.py, `-` read as `_`) and its output dir
# (tests/fixtures/<x>). p3b-v6 first restarts the guest's TRex on the linux_based stack (vm/stack.sh) and puts it
# back on legacy afterwards; the snapshot itself always stays legacy. TREX_VM_STACK=linux_based does the same for the
# real-tier tests (make check-real); the default and every other recorder run legacy.
# p5 records the monitor/daemon fixtures, p5-stf the daemon-started STF run, file listings and upload gate; afterwards (whatever the recorder's rc) it stops the guest's daemon and puts TRex back on
# the stateless stack (vm/daemon.sh stop, vm/mode.sh stateless), and exits with the recorder's rc.
# TREX_VM_MARK=restarts_trex (make check-daemon-real, jammy only) runs the tests that restart the guest's TRex; the default
# mark is `real and not restarts_trex`. The snapshot is never saved by any of this.
# The guest comes from TREX_VM_GUEST (vm/guests.sh; default jammy); recorders run on jammy only.
# TREX_VM_SNAPSHOT overrides the snapshot tag (default trex_ready).
set -u
case "${TREX_VM_RECORD:-}" in ""|p2|p3|p3a|p3b|p3b-v6|p3c|p5|p5-stf|netem) ;; *) echo "UNAVAILABLE: TREX_VM_RECORD must be p2, p3, p3a, p3b, p3b-v6, p3c, p5, p5-stf or netem"; exit 2;; esac
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(dirname "$HERE")
. "$HERE/guests.sh" || exit 2
W=$TREX_VM_DIR
if [ -n "${TREX_VM_RECORD:-}" ] && [ "$TREX_VM_CODENAME" != jammy ]; then
  echo "UNAVAILABLE: TREX_VM_RECORD records from jammy only"; exit 2
fi
case "${TREX_VM_MARK:-}" in
  "") ;;
  restarts_trex) [ "$TREX_VM_CODENAME" = jammy ] || { echo "UNAVAILABLE: TREX_VM_MARK=restarts_trex runs on jammy only"; exit 2; } ;;
  *) echo "UNAVAILABLE: TREX_VM_MARK must be empty or restarts_trex"; exit 2 ;;
esac
STACK=${TREX_VM_STACK:-legacy}; [ "${TREX_VM_RECORD:-}" = p3b-v6 ] && STACK=linux_based
case "$STACK" in legacy|linux_based) ;; *) echo "UNAVAILABLE: TREX_VM_STACK must be legacy or linux_based"; exit 2;; esac
[ -f "$W/trex.qcow2" ] || { echo "UNAVAILABLE: no VM image at $W (see vm/README.md)"; exit 2; }
if trex_vm_running || trex_port_open "$TREX_VM_SSH_PORT"; then
  echo "UNAVAILABLE: a trex VM is already running"; exit 2
fi
"$HERE/launch.sh" "${TREX_VM_SNAPSHOT:-trex_ready}" & qemu=$!
# Quit only this run's QEMU ($! is QEMU: the systemd-run scope execs it); a stale pidfile or another guest's is left alone.
trap 'kill -0 $qemu 2>/dev/null && printf "quit\n" | socat -t 5 - UNIX-CONNECT:$W/monitor.sock >/dev/null 2>&1; wait $qemu 2>/dev/null' EXIT
up=0
for i in $(seq 1 50); do
  kill -0 $qemu 2>/dev/null || break
  "$HERE/ssh.sh" true 2>/dev/null && { up=1; break; }
  sleep 0.2
done
if [ $up -ne 1 ] || ! kill -0 $qemu 2>/dev/null; then
  echo "UNAVAILABLE: VM did not come up (see $W/console.log)"; exit 2
fi
if ! "$HERE/ssh.sh" "sudo date -u -s @$(date +%s.%N)" >/dev/null; then   # guest clock lags by time spent saved (spike 2)
  echo "UNAVAILABLE: could not step the guest clock over ssh"; exit 2
fi
if [ "$STACK" = linux_based ] && ! "$HERE/stack.sh" linux_based; then
  echo "UNAVAILABLE: could not restart the guest TRex on linux_based"; exit 2
fi
if [ -n "${TREX_VM_RECORD:-}" ]; then
  cd "$REPO" && systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 -- \
    uv run --project backend python -I -B "vm/record_${TREX_VM_RECORD//-/_}.py" "tests/fixtures/${TREX_VM_RECORD}"
  rc=$?
  if [ "$TREX_VM_RECORD" = p5 ] || [ "$TREX_VM_RECORD" = p5-stf ]; then "$HERE/daemon.sh" stop; "$HERE/mode.sh" stateless; fi
  [ "$STACK" = linux_based ] && "$HERE/stack.sh" legacy
  exit $rc
else
  cd "$REPO" && TREX_REAL_HOST=127.0.0.1 TREX_VM_MONITOR=$W/monitor.sock \
    systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 -- uv run --project backend pytest -m "${TREX_VM_MARK:-real and not restarts_trex}" -v -s
  rc=$?
  [ "$STACK" = linux_based ] && "$HERE/stack.sh" legacy
  exit $rc
fi
