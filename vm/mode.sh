#!/bin/bash
# Usage: vm/mode.sh stateful|stateless|emu|stop
# Switch the running guest's TRex between its two stacks (P5). `stateless` stops TRex and any trex-emu, then runs
# vm/stack.sh legacy; `stateful` stops TRex and starts `t-rex-64 -f cap2/sfr2.yaml` (the spec §8 command) detached,
# then waits for a `trex-global` PUB message (vm/pub_ready.py, one attempt of 60 s); `stop` stops _t-rex-64
# (SIGINT, then SIGKILL after 15 s).
# `emu` stops TRex and starts `t-rex-64 -i --iom 0 --no-scapy-server --emu --cfg /etc/trex_cfg.yaml` detached (log
# /tmp/trex-emu-mode.log; TRex starts trex-emu itself), then waits for EMU to answer api_sync_v2 and ping
# (vm/emu_ready.py, one attempt of 60 s). `stop`, `emu` and `stateless` also stop any trex-emu still running
# (TERM, KILL after 15 s), since the wrapper's cleanup stops EMU only when TRex exits cleanly.
# The snapshot is never saved here, so the next restore of the snapshot is stateless again whatever happened.
# Seams: TREX_VM_SSH (default vm/ssh.sh), TREX_VM_STACK_SH (default vm/stack.sh), TREX_VM_PUB_READY (default
# `uv run --project backend python -I -B vm/pub_ready.py`), TREX_VM_EMU_READY (default
# `uv run --project backend python -I -B vm/emu_ready.py`).
set -u
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(dirname "$HERE")
. "$HERE/guests.sh" || exit 2   # exports the guest ports for vm/pub_ready.py
SSH=${TREX_VM_SSH:-$HERE/ssh.sh}
STACK_SH=${TREX_VM_STACK_SH:-$HERE/stack.sh}
PUB_READY=${TREX_VM_PUB_READY:-uv run --project backend python -I -B vm/pub_ready.py}
EMU_READY=${TREX_VM_EMU_READY:-uv run --project backend python -I -B vm/emu_ready.py}
LOG=/tmp/trex-stateful.log
EMU_LOG=/tmp/trex-emu-mode.log

# stop_trex: the stack.sh stop sequence. pkill/pgrep -x match the process NAME: -f would also match this ssh command.
stop_trex() {
  "$SSH" "sudo pkill -INT -x _t-rex-64; for i in \$(seq 1 75); do pgrep -x _t-rex-64 >/dev/null || exit 0; sleep 0.2; done;
          sudo pkill -KILL -x _t-rex-64; sleep 1; ! pgrep -x _t-rex-64 >/dev/null" </dev/null \
    || { echo "TRex did not stop in the guest"; return 1; }
}

# stop_emu: stop any trex-emu left in the guest (hosted by a killed TRex, or standalone from vm/emu.sh); fails if it stays.
stop_emu() {
  "$SSH" "sudo pkill -TERM -x trex-emu; for i in \$(seq 1 75); do pgrep -x trex-emu >/dev/null || exit 0; sleep 0.2; done;
          sudo pkill -KILL -x trex-emu; sleep 1; ! pgrep -x trex-emu >/dev/null" </dev/null \
    || { echo "trex-emu did not stop in the guest"; return 1; }
}

# stateful: stop, start the spec command inside a root shell (setsid and every descriptor redirected detach it
# from the ssh session; the old log goes first because root cannot open another user's file in sticky /tmp).
stateful() {
  stop_trex || return 1
  "$SSH" "sudo sh -c 'rm -f $LOG; cd /opt/trex/v3.08 && setsid nohup ./t-rex-64 -f cap2/sfr2.yaml -d 120 -l 1000 --rx-check 100 -c 1 >$LOG 2>&1 </dev/null &'" </dev/null \
    || { echo "could not start TRex"; return 1; }
  # shellcheck disable=SC2086  # the seam is a command line
  if ! (cd "$REPO" && $PUB_READY --timeout 60); then
    echo "TRex did not publish in stateful mode; guest log $LOG"
    "$SSH" "tail -n 20 $LOG" </dev/null
    return 1
  fi
}

# emu: stop, start TRex with its EMU server (--emu) detached as stateful does, then wait for EMU to answer.
emu() {
  stop_trex || return 1
  stop_emu || return 1
  "$SSH" "sudo sh -c 'rm -f $EMU_LOG; cd /opt/trex/v3.08 && setsid nohup ./t-rex-64 -i --iom 0 --no-scapy-server --emu --cfg /etc/trex_cfg.yaml >$EMU_LOG 2>&1 </dev/null &'" </dev/null \
    || { echo "could not start TRex"; return 1; }
  # shellcheck disable=SC2086  # the seam is a command line
  if ! (cd "$REPO" && $EMU_READY --timeout 60); then
    echo "EMU did not answer under TRex; guest log $EMU_LOG"
    "$SSH" "tail -n 20 $EMU_LOG" </dev/null
    return 1
  fi
}

case "${1:-}" in
  emu) emu; exit $? ;;
  stateful) stateful; exit $? ;;
  stateless) stop_trex && stop_emu && exec "$STACK_SH" legacy; exit $? ;;
  stop) stop_trex && stop_emu; exit $? ;;
  *) sed -n '2,3p' "$0"; exit 2 ;;
esac
