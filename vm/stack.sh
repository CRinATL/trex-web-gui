#!/bin/bash
# Usage: vm/stack.sh legacy|linux_based
# Restart TRex in the running guest on the given stack: install vm/guest/trex_cfg.yaml (legacy) or
# vm/guest/trex_cfg_linux_based.yaml as /etc/trex_cfg_<stack>.yaml, stop _t-rex-64 (SIGINT, then SIGKILL after
# 15 s), start it on that file, and wait until a client can connect (vm/ready.py). The snapshot is never saved,
# so the next restore of trex_ready is legacy again whatever happened here.
set -u
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(dirname "$HERE")
. "$HERE/guests.sh" || exit 2   # exports the guest's ports for vm/ready.py

main() {
  local stack=${1:-}
  case "$stack" in
    legacy) cfg=$HERE/guest/trex_cfg.yaml ;;
    linux_based) cfg=$HERE/guest/trex_cfg_linux_based.yaml ;;
    *) sed -n '2,6p' "$0"; return 2 ;;
  esac
  "$HERE/ssh.sh" "sudo tee /etc/trex_cfg_$stack.yaml >/dev/null" <"$cfg" || { echo "could not install the config"; return 1; }
  # pgrep/pkill -x match the process NAME: -f would also match this very ssh command line
  "$HERE/ssh.sh" "sudo pkill -INT -x _t-rex-64; for i in \$(seq 1 75); do pgrep -x _t-rex-64 >/dev/null || exit 0; sleep 0.2; done;
                  sudo pkill -KILL -x _t-rex-64; sleep 1; ! pgrep -x _t-rex-64 >/dev/null" </dev/null \
    || { echo "TRex did not stop in the guest"; return 1; }
  # Backgrounded INSIDE the root shell: `sudo cmd &` keeps sudo alive as the parent (Ubuntu's use_pty), and the
  # ssh session then never ends. setsid and every descriptor redirected detach TRex from the session.
  # The old log goes first: one left by another user cannot be opened by root in sticky /tmp (protected_regular).
  "$HERE/ssh.sh" "sudo sh -c 'rm -f /tmp/trex-$stack.log; cd /opt/trex/v3.08 && setsid nohup ./t-rex-64 -i --iom 0 --no-scapy-server \
                  --cfg /etc/trex_cfg_$stack.yaml >/tmp/trex-$stack.log 2>&1 </dev/null &'" </dev/null \
    || { echo "could not start TRex"; return 1; }
  local t0; t0=$(mktemp); date +%s.%N >"$t0"
  (cd "$REPO" && uv run --project backend python -I -B vm/ready.py "$t0"); local rc=$?
  rm -f "$t0"
  [ $rc -eq 0 ] || { echo "TRex on $stack did not answer; guest log /tmp/trex-$stack.log"; return 1; }
  echo "TRex restarted on the $stack stack"
}

main "$@"; exit $?
