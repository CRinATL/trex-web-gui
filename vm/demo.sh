#!/bin/bash
# Usage: vm/demo.sh up|down|status [--all]
# A manual or browser session against the real guest: `up` restores trex_ready, steps the guest clock,
# builds the frontend and serves it on the guest's GUI port (jammy 8765; vm/guests.sh) with the TRex server
# prefilled; both keep running after this script exits. `down` stops the GUI (SIGINT: it closes the TRex session
# first) and the VM.
# The guest comes from TREX_VM_GUEST (default jammy). It is single-user: `make check-real` refuses to start while
# it runs. `status --all` reports every guest. For another machine, `up` prints the SSH tunnel; the GUI binds
# loopback only and its Origin check accepts only its own port.
# `up` makes a fresh access token (kept in $W/demo-token, mode 0600) and prints the login URL; `status` prints it
# again while the GUI runs. Open that URL, not the bare address: every /api route needs the cookie it sets.
# DEMO_SH_LIB=1 makes `. vm/demo.sh` define the functions only. TREX_VM_DEMO_DIRS="codename=dir ..." overrides the
# work dir of the named rows for `status --all` and the shared-dist check (a test seam).
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); REPO=$(dirname "$HERE")
. "$HERE/guests.sh" || exit 2
W=$TREX_VM_DIR; PORT=$TREX_VM_GUI_PORT; GUI_PID=$W/demo-gui.pid; TOKEN_FILE=$W/demo-token

main() {
  case "${1:-}" in
    up) up ;;
    down) down ;;
    status) if [ "${2:-}" = --all ]; then status_all; else status; fi ;;
    *) sed -n '2,/^set -u/{/^#/p}' "$0"; return 2 ;;
  esac
}

gui_running() { [ -f "$GUI_PID" ] && kill -0 "$(cat "$GUI_PID")" 2>/dev/null; }
port_open() { trex_port_open "$1"; }

# row_dir <codename>: the row's work dir; TREX_VM_DEMO_DIRS ("codename=dir ...") overrides it.
row_dir() {
  local kv
  for kv in ${TREX_VM_DEMO_DIRS:-}; do
    [ "${kv%%=*}" = "$1" ] && { echo "${kv#*=}"; return 0; }
  done
  (unset TREX_VM_DIR TREX_VM_DL TREX_VM_SHARED TREX_VM_CODENAME; TREX_VM_GUEST=$1; . "$HERE/guests.sh" && echo "$TREX_VM_DIR")
}

# other_demo_serving: prints the codename of another guest whose demo GUI runs (its demo-gui.pid names a live
# trex-web-gui serve process, so a reused PID does not count) and returns 0; returns 1 when there is none.
other_demo_serving() {
  local row dir pid cmd
  while read -r row; do
    [ "$row" = "$TREX_VM_CODENAME" ] && continue
    dir=$(row_dir "$row") || continue
    { read -r pid <"$dir/demo-gui.pid"; } 2>/dev/null || continue
    [[ ${pid:-} =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null || continue
    cmd=$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null) || continue
    case $cmd in *trex-web-gui*serve*) echo "$row"; return 0 ;; esac
  done < <("$HERE/guests.sh" --list)
  return 1
}

wait_for() {   # $1 = tries (0.3 s apart), rest = command
  local n=$1; shift
  for _ in $(seq 1 "$n"); do "$@" && return 0; read -rt 0.3 </dev/zero 2>/dev/null || true; done
  return 1
}

# daemon_flag: the --daemon-port flag for this guest. Jammy forwards the daemon at 8090, the client default, so it
# prints nothing and its command line is unchanged; the other rows forward $TREX_VM_DAEMON_PORT (vm/guests.sh).
daemon_flag() {
  [ "$TREX_VM_CODENAME" = jammy ] || echo "--daemon-port $TREX_VM_DAEMON_PORT"
}

up() {
  [ -f "$W/trex.qcow2" ] || { echo "no VM image at $W (see vm/README.md)"; return 2; }
  if trex_vm_running || port_open "$TREX_VM_SSH_PORT"; then
    echo "a trex VM is already running (vm/demo.sh status)"; return 2
  fi
  if port_open $PORT; then echo "port $PORT is already in use"; return 2; fi
  setsid nohup "$HERE/launch.sh" "${TREX_VM_SNAPSHOT:-trex_ready}" >"$W/demo-qemu.log" 2>&1 </dev/null &
  wait_for 50 "$HERE/ssh.sh" true 2>/dev/null || { echo "VM did not come up (see $W/console.log)"; return 1; }
  "$HERE/ssh.sh" "sudo date -u -s @$(date +%s.%N)" >/dev/null || { echo "could not step the guest clock"; return 1; }
  wait_for 30 port_open "$TREX_REAL_SYNC_PORT" || { echo "TRex RPC $TREX_REAL_SYNC_PORT not answering in the guest"; return 1; }
  local other
  if other=$(other_demo_serving) && [ -f "$REPO/frontend/dist/index.html" ]; then
    echo "frontend/dist is served by the $other demo: not rebuilt"
  else
    npm --prefix "$REPO/frontend" run build >"$W/demo-build.log" 2>&1 || { echo "frontend build failed: $W/demo-build.log"; return 1; }
  fi
  # The token and the log that collects the backend's banner (which carries the login URL) are private: umask 077.
  rm -f "$TOKEN_FILE" "$W/demo-gui.log"      # so a pre-existing wider mode is not kept
  (umask 077; python3 -c 'import secrets;print(secrets.token_urlsafe(32))' >"$TOKEN_FILE" && : >"$W/demo-gui.log") \
    || { echo "could not write $TOKEN_FILE"; return 1; }
  # One simple command in the background, so $! is the process that execs setsid -> nohup -> systemd-run
  # --scope -> uv: the pid file names uv, which forwards a SIGINT to the server. (Backgrounding a list
  # such as `cd && cmd` would record a bash subshell, and a SIGINT to it orphans the server.)
  cd "$REPO" || return 1
  local server=127.0.0.1 # jammy keeps the bare host (its ports are the client defaults)
  [ "$TREX_VM_CODENAME" = jammy ] || server=127.0.0.1:$TREX_REAL_SYNC_PORT:$TREX_REAL_ASYNC_PORT
  local daemon; daemon=$(daemon_flag)
  TREX_WEB_GUI_TOKEN=$(cat "$TOKEN_FILE") setsid nohup systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 -- \
    uv run --project backend trex-web-gui serve --port $PORT --server $server $daemon >"$W/demo-gui.log" 2>&1 </dev/null &
  echo $! >"$GUI_PID"
  wait_for 60 curl -sf -o /dev/null "http://127.0.0.1:$PORT/" || { echo "GUI did not answer: $W/demo-gui.log"; return 1; }
  echo "up: TRex guest (snapshot ${TREX_VM_SNAPSHOT:-trex_ready}) and the GUI at http://127.0.0.1:$PORT (server $server)"
  echo "open: $(login_url 127.0.0.1)"
  tunnel
}

login_url() { echo "http://$1:$PORT/api/auth/login?token=$(cat "$TOKEN_FILE")"; }

tunnel() {
  local ips; ips=$(ip -4 -o addr show scope global 2>/dev/null | awk '$2 !~ /^(vmnet|virbr|docker|br-)/ {split($4,a,"/"); print a[1]}')
  echo "from another machine on the network (this host: $(hostname)):"
  for ip in $ips; do echo "  ssh -N -L $PORT:127.0.0.1:$PORT $USER@$ip"; done
  echo "  then open $(login_url localhost) and connect to 127.0.0.1 (keep the local port $PORT)"
}

down() {
  if gui_running; then
    local pid; pid=$(cat "$GUI_PID")
    kill -INT "$pid"   # uv forwards it once; a second SIGINT would only print the "ignored" notice
    wait_for 60 bash -c "! kill -0 $pid 2>/dev/null" || echo "GUI still running after 18 s (pid $pid)"
  elif port_open $PORT; then
    echo "port $PORT is served by a GUI this script did not start; stop it yourself"
  fi
  rm -f "$GUI_PID"
  if trex_vm_running; then
    printf "quit\n" | socat -t 5 - "UNIX-CONNECT:$W/monitor.sock" >/dev/null 2>&1
    wait_for 30 bash -c "! [ -f '$W/qemu.pid' ] || ! kill -0 \$(cat '$W/qemu.pid') 2>/dev/null" \
      || { echo "VM still running"; return 1; }
  fi
  echo "down"
}

status() {
  if trex_vm_running; then echo "VM: running (pid $(cat "$W/qemu.pid"))"; else echo "VM: stopped"; fi
  if gui_running; then echo "GUI: running (pid $(cat "$GUI_PID")) at http://127.0.0.1:$PORT"
    [ -f "$TOKEN_FILE" ] && echo "open: $(login_url 127.0.0.1)"
  elif port_open $PORT; then echo "GUI: port $PORT is served, not by demo.sh"
  else echo "GUI: stopped"; fi
}

# status_all: `status` for every guest row, each in a subshell with that row's variables.
status_all() {
  local row dir rc=0
  while read -r row; do
    echo "$row:"
    dir=$(row_dir "$row")
    if [ -z "$dir" ]; then echo "VM: unknown (could not resolve the work dir)"; rc=1; continue; fi
    (
      unset TREX_VM_DIR TREX_VM_DL TREX_VM_SHARED TREX_VM_CODENAME
      export TREX_VM_GUEST=$row
      . "$HERE/guests.sh" || exit 2
      TREX_VM_DIR=$dir; W=$dir; PORT=$TREX_VM_GUI_PORT; GUI_PID=$W/demo-gui.pid; TOKEN_FILE=$W/demo-token
      status
    )
  done < <("$HERE/guests.sh" --list)
  return $rc
}

if [ -n "${DEMO_SH_LIB:-}" ] && [ "${BASH_SOURCE[0]}" != "$0" ]; then return 0; fi
main "$@"; exit $?
