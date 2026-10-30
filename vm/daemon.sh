#!/bin/bash
# Usage: vm/daemon.sh start|stop|status
# Run TRex's daemon (trex_daemon_server) in the running guest (P5, D4). `start` runs `start-live` under setsid nohup with
# TREX_DAEMON_LOG=/tmp/trex-daemon.log and records its pid in /tmp/trex-daemon.pid, then polls connectivity_check on
# the forwarded port ($TREX_VM_DAEMON_PORT, 8090 on jammy) for 20 s. `stop` sends TERM to that pid (only while its
# cmdline names trex_daemon_server), KILL after 15 s, and fails if the port still answers 2 s later. `status`
# probes the port, not the pidfile. Seam: TREX_VM_SSH (default vm/ssh.sh).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/guests.sh" || exit 2
SSH=${TREX_VM_SSH:-$HERE/ssh.sh}
PORT=$TREX_VM_DAEMON_PORT
LOG=/tmp/trex-daemon.log

# answers: 0 iff the daemon port answers connectivity_check with result true.
answers() {
  curl -s --noproxy '*' --max-time 2 -X POST -H 'Content-Type: application/json' \
    -d '{"jsonrpc": "2.0", "id": 1, "method": "connectivity_check", "params": []}' "http://127.0.0.1:$PORT/" 2>/dev/null \
    | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("result") is True else 1)' 2>/dev/null
}

start() {
  "$SSH" "sudo sh -c 'rm -f $LOG; cd /opt/trex/v3.08 && TREX_DAEMON_LOG=$LOG setsid nohup ./trex_daemon_server start-live -p 8090 >$LOG 2>&1 </dev/null & echo \$! >/tmp/trex-daemon.pid'" </dev/null \
    || { echo "could not start the daemon"; return 1; }
  local i
  for i in $(seq 1 40); do
    answers && { echo "daemon: running"; return 0; }
    sleep 0.5
  done
  echo "daemon did not answer on $PORT; guest log $LOG"
  "$SSH" "tail -n 20 $LOG" </dev/null
  return 1
}

stop() {
  # A pid is signalled only while its cmdline names the daemon: a recycled pid is left alone.
  "$SSH" "sudo sh -c 'p=\$(cat /tmp/trex-daemon.pid 2>/dev/null); [ -n \"\$p\" ] && grep -qa trex_daemon_server /proc/\$p/cmdline && kill -TERM \$p'" </dev/null
  # Exits 0 iff the pid is still the daemon after 15 s.
  if "$SSH" "sudo sh -c 'p=\$(cat /tmp/trex-daemon.pid 2>/dev/null); for i in \$(seq 1 75); do [ -n \"\$p\" ] && grep -qa trex_daemon_server /proc/\$p/cmdline || exit 1; sleep 0.2; done; exit 0'" </dev/null; then
    "$SSH" "sudo sh -c 'p=\$(cat /tmp/trex-daemon.pid 2>/dev/null); [ -n \"\$p\" ] && grep -qa trex_daemon_server /proc/\$p/cmdline && kill -KILL \$p'" </dev/null
  fi
  "$SSH" "sudo rm -f /tmp/trex-daemon.pid" </dev/null
  sleep 2
  if answers; then echo "daemon still answers on $PORT after stop"; return 1; fi
  echo "daemon: stopped"
}

status() { if answers; then echo "daemon: running"; else echo "daemon: stopped"; fi; }

case "${1:-}" in
  start) start; exit $? ;;
  stop) stop; exit $? ;;
  status) status; exit 0 ;;
  *) sed -n '2,2p' "$0"; exit 2 ;;
esac
