#!/bin/bash
# Usage: vm/emu.sh start|stop|status
# Run EMU standalone (no TRex) in the running guest (P7, spec 5.3 layer 1). `start` refuses while any trex-emu or TRex (_t-rex-64) runs in
# the guest, runs `LD_LIBRARY_PATH=/opt/trex/v3.08/so ./trex_emu/trex-emu -p 4510` under setsid nohup (NO -d: it accepts
# one handshake per process) with log /tmp/trex-emu.log, records its pid in /tmp/trex-emu.pid, then waits for EMU to
# answer (vm/emu_ready.py, 60 s). `stop` sends TERM to that pid (only while its cmdline names trex-emu), KILL after
# 15 s, and removes the pidfile. `status` reads the guest's process list. Seams: TREX_VM_SSH (default vm/ssh.sh),
# TREX_VM_EMU_READY (default `uv run --project backend python -I -B vm/emu_ready.py`).
set -u
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(dirname "$HERE")
. "$HERE/guests.sh" || exit 2   # exports TREX_VM_EMU_PORT for vm/emu_ready.py
SSH=${TREX_VM_SSH:-$HERE/ssh.sh}
EMU_READY=${TREX_VM_EMU_READY:-uv run --project backend python -I -B vm/emu_ready.py}
LOG=/tmp/trex-emu.log
PIDFILE=/tmp/trex-emu.pid

running() { "$SSH" "pgrep -x trex-emu >/dev/null" </dev/null; }

start() {
  if running; then echo "a trex-emu already runs in the guest; stop it first"; return 1; fi
  if "$SSH" "pgrep -x _t-rex-64 >/dev/null" </dev/null; then echo "TRex runs in the guest; stop it first (vm/mode.sh stop)"; return 1; fi
  "$SSH" "sudo sh -c 'rm -f $LOG; cd /opt/trex/v3.08 && LD_LIBRARY_PATH=/opt/trex/v3.08/so setsid nohup ./trex_emu/trex-emu -p 4510 >$LOG 2>&1 </dev/null & echo \$! >$PIDFILE'" </dev/null \
    || { echo "could not start EMU"; return 1; }
  # shellcheck disable=SC2086  # the seam is a command line
  if ! (cd "$REPO" && $EMU_READY --timeout 60); then
    echo "EMU did not answer; guest log $LOG"
    "$SSH" "tail -n 20 $LOG" </dev/null
    return 1
  fi
}

stop() {
  # A pid is signalled only while its cmdline names trex-emu: a recycled pid is left alone.
  "$SSH" "sudo sh -c 'p=\$(cat $PIDFILE 2>/dev/null); [ -n \"\$p\" ] && grep -qa trex-emu /proc/\$p/cmdline && kill -TERM \$p'" </dev/null
  # Exits 0 iff the pid is still EMU after 15 s.
  if "$SSH" "sudo sh -c 'p=\$(cat $PIDFILE 2>/dev/null); for i in \$(seq 1 75); do [ -n \"\$p\" ] && grep -qa trex-emu /proc/\$p/cmdline || exit 1; sleep 0.2; done; exit 0'" </dev/null; then
    "$SSH" "sudo sh -c 'p=\$(cat $PIDFILE 2>/dev/null); [ -n \"\$p\" ] && grep -qa trex-emu /proc/\$p/cmdline && kill -KILL \$p'" </dev/null
  fi
  "$SSH" "sudo rm -f $PIDFILE" </dev/null
  if running; then echo "emu still runs after stop"; return 1; fi
  echo "emu: stopped"
}

status() { if running; then echo "emu: running"; else echo "emu: stopped"; fi; }

case "${1:-}" in
  start) start; exit $? ;;
  stop) stop; exit $? ;;
  status) status; exit 0 ;;
  *) sed -n '2,2p' "$0"; exit 2 ;;
esac
