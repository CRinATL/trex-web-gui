#!/bin/bash
# Usage: build.sh <guest> [--rebuild]   |   build.sh --is-protected <dir>
# Builds a test guest from scratch in its own work dir ($TREX_VM_DIR, from vm/guests.sh): download and verify the
# image and TRex, make the cloud-init seed and the overlay, cold-boot, run vm/guest/setup.sh, start TRex, wait for
# RPC, `savevm trex_ready`, then reload the snapshot to time it. jammy is built by hand (vm/README.md) and is
# never built here. Every refusal exits 2 before any file operation or tool: an unknown guest; jammy; a
# TREX_VM_DIR, TREX_VM_DL or TREX_VM_SHARED set by hand that differs from the row's; any of those dirs or the
# tarball dir inside, or equal to, a protected dir (/var/tmp/trex-vm, plus each TREX_VM_PROTECT_ALSO entry, compared
# by realpath -m); a QEMU using the dir or one of its ports; an existing trex.qcow2 without --rebuild.
# --rebuild then removes only trex.qcow2, seed.iso, seed/user-data, seed/meta-data, qemu.pid, console.log and
# known_hosts, and keeps the SSH key pair. --is-protected <dir> exits 0 if the dir is protected, else 1.
set -u
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(dirname "$HERE")

# is_protected <path>: 0 iff <path> equals or lies under a protected dir (component-wise on realpath -m output).
# An empty realpath on either side counts as protected.
is_protected() {
  local p prot
  p=$(realpath -m -- "$1" 2>/dev/null) || return 0
  [ -n "$p" ] || return 0
  for prot in /var/tmp/trex-vm ${TREX_VM_PROTECT_ALSO:-}; do
    prot=$(realpath -m -- "$prot" 2>/dev/null) || return 0
    [ -n "$prot" ] || return 0
    if [[ $p == "$prot" || $p == "$prot"/* ]]; then return 0; fi
  done
  return 1
}

usage() { sed -n '2p' "$0" | sed 's/^# //' >&2; echo "guests: $(IFS=,; echo "${names[*]}")" >&2; }

if [ "${1:-}" = --is-protected ]; then
  [ $# -eq 2 ] || { echo "usage: build.sh --is-protected <dir>" >&2; exit 2; }
  is_protected "$2"; exit $?
fi

mapfile -t names < <("$HERE/guests.sh" --list)
guest=${1:-} rebuild=0
if [ $# -ge 2 ]; then [ "$2" = --rebuild ] && [ $# -eq 2 ] && rebuild=1 || guest=; fi
case " ${names[*]} " in *" $guest "*) [ -n "$guest" ] || { usage; exit 2; } ;; *) usage; exit 2 ;; esac
if [ "$guest" = jammy ]; then echo "jammy is built by hand; see vm/README.md" >&2; exit 2; fi

if [[ ${TREX_VM_BASE:-/var/tmp} != /* ]]; then echo "TREX_VM_BASE must be an absolute path" >&2; exit 2; fi

# The three dirs may not be set by hand to anything but the row's own value (TREX_VM_BASE is the only redirect).
given_dir=${TREX_VM_DIR:-} given_dl=${TREX_VM_DL:-} given_shared=${TREX_VM_SHARED:-}
unset TREX_VM_DIR TREX_VM_DL TREX_VM_SHARED
export TREX_VM_GUEST=$guest
. "$HERE/guests.sh" || exit 2
for v in DIR:"$given_dir" DL:"$given_dl" SHARED:"$given_shared"; do
  name=TREX_VM_${v%%:*} given=${v#*:}
  if [ -n "$given" ] && [ "$given" != "${!name}" ]; then
    echo "$name=$given differs from the $guest row's ${!name}; unset $name (TREX_VM_BASE is the only redirect)" >&2
    exit 2
  fi
done
W=$TREX_VM_DIR
for d in "$TREX_VM_DIR" "$TREX_VM_DL" "$TREX_VM_SHARED" "$TREX_VM_DL_TREX"; do
  if is_protected "$d"; then echo "$d is a protected dir (/var/tmp/trex-vm or TREX_VM_PROTECT_ALSO): refusing" >&2; exit 2; fi
done
# The dirs, and the files --rebuild and the steps write, may not be links, and the dirs must be mine and not
# group- or world-writable (they live under world-writable /var/tmp). Jammy's are never reached: it is refused above.
for d in "$TREX_VM_DIR" "$TREX_VM_SHARED" "$TREX_VM_DL" "$TREX_VM_DL_TREX" "$(dirname "$TREX_VM_TLS")"; do
  if why=$(trex_dir_unsafe "$d"); then echo "$why: refusing" >&2; exit 2; fi
done
# check_entries: refuses (exit 2) when a file build.sh writes or removes in $W is a symlink.
check_entries() {
  local f
  for f in seed seed/user-data seed/meta-data seed.iso trex.qcow2 qemu.pid console.log known_hosts id_ed25519 \
    id_ed25519.pub; do
    if [ -L "$W/$f" ]; then echo "$W/$f is a symlink: refusing" >&2; exit 2; fi
  done
}
check_entries

# in_use: prints why and returns 0 if a QEMU uses this dir.
in_use() {
  local pid f argv port
  { read -r pid <"$W/qemu.pid"; } 2>/dev/null || true
  if [[ ${pid:-} =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    echo "process $pid from $W/qemu.pid is running"; return 0
  fi
  for f in /proc/[0-9]*/cmdline; do
    pid=${f#/proc/}; pid=${pid%%/*}
    [ "$pid" = "$$" ] || [ "$pid" = "$BASHPID" ] && continue
    argv=()
    { mapfile -d '' argv <"$f"; } 2>/dev/null || continue
    if [[ "${argv[*]:-}" == *"file=$W/trex.qcow2,"* ]]; then echo "process $pid is running with $W/trex.qcow2"; return 0; fi
  done
  for port in $TREX_VM_SSH_PORT $TREX_REAL_ASYNC_PORT $TREX_REAL_SYNC_PORT $TREX_VM_EMU_PORT $TREX_VM_DAEMON_PORT \
    $TREX_VM_GUI_PORT; do
    if trex_port_open "$port"; then echo "port $port is in use: a guest is running"; return 0; fi
  done
  return 1
}
if why=$(in_use); then echo "$guest: $why" >&2; exit 2; fi
if [ -e "$W/trex.qcow2" ] && [ $rebuild -eq 0 ]; then
  echo "$W/trex.qcow2 exists; pass --rebuild to rebuild it" >&2; exit 2
fi
if [ $rebuild -eq 1 ]; then
  for f in trex.qcow2 seed.iso seed/user-data seed/meta-data qemu.pid console.log known_hosts; do rm -f "$W/$f"; done
fi

die() { echo "build.sh: $*" >&2; exit 1; }
now() { echo "${EPOCHREALTIME/,/.}"; }
elapsed() { awk -v a="$1" -v b="$2" 'BEGIN{printf "%.1f", b-a}'; }
mon() { printf '%s\n' "$1" | socat -t "${2:-5}" - "UNIX-CONNECT:$W/monitor.sock"; }
QPID=""
# own_qemu_live: 0 iff the QEMU this run recorded is still this guest's live process.
own_qemu_live() { [ -n "$QPID" ] && kill -0 "$QPID" 2>/dev/null && trex_vm_running && [ "$(cat "$W/qemu.pid" 2>/dev/null)" = "$QPID" ]; }
t0file=""
# cleanup: EXIT trap. Quits only this run's QEMU and removes the t0 temp file.
cleanup() { if own_qemu_live; then mon quit >/dev/null 2>&1; fi; if [ -n "$t0file" ]; then rm -f "$t0file"; fi; }
# record_qemu: remembers the PID QEMU wrote to qemu.pid, once it is live.
record_qemu() { local p; p=$(cat "$W/qemu.pid" 2>/dev/null) && [[ $p =~ ^[0-9]+$ ]] && kill -0 "$p" 2>/dev/null && QPID=$p; }
# wait_gone: waits up to 30 s for the recorded QEMU to exit.
wait_gone() { local i; for i in $(seq 1 100); do kill -0 "$QPID" 2>/dev/null || return 0; read -rt 0.3 </dev/zero 2>/dev/null || true; done; return 1; }
# launch_and_wait_ssh <args>: starts launch.sh detached, then waits (up to 180 s, or until the recorded QEMU dies)
# for ssh; sets QPID.
launch_and_wait_ssh() {
  rm -f "$W/qemu.pid"
  setsid nohup "$HERE/launch.sh" "$@" >"$W/build-qemu.log" 2>&1 </dev/null &
  local lpid=$!
  local end=$((SECONDS + 180))
  while [ $SECONDS -lt $end ]; do
    [ -n "$QPID" ] || record_qemu
    if [ -n "$QPID" ] && ! kill -0 "$QPID" 2>/dev/null; then return 1; fi
    if [ -z "$QPID" ] && ! kill -0 "$lpid" 2>/dev/null; then return 1; fi   # launch.sh died before QEMU wrote its pidfile
    "$HERE/ssh.sh" true </dev/null >/dev/null 2>&1 && { [ -n "$QPID" ] || record_qemu; return 0; }
    read -rt 0.3 </dev/zero 2>/dev/null || true
  done
  return 1
}

trex_mkdir700 "$W" || die "cannot create $W"                                                      # step 1
if [ ! -f "$TREX_VM_TLS" ]; then                                                                      # step 2
  src=${TREX_VM_BASE:-/var/tmp}/trex-vm/tls/bundle.pem
  [ -f "$src" ] || { echo "no TLS bundle at $TREX_VM_TLS or $src (see vm/README.md)" >&2; exit 2; }
  trex_mkdir700 "$(dirname "$TREX_VM_TLS")" && install -m 444 "$src" "$TREX_VM_TLS" || die "cannot copy the TLS bundle"
fi
"$HERE/download.sh" --verify-only >/dev/null 2>&1 || "$HERE/download.sh" || die "download failed"                      # step 3
cd "$W" || die "cannot enter $W"
[ "$(stat -c %u .)" = "$(id -u)" ] || die "$W is not owned by the current user"
check_entries
[ -f id_ed25519 ] || ssh-keygen -t ed25519 -N '' -f id_ed25519 || die "ssh-keygen failed"             # step 4
mkdir -p seed || die "cannot create $W/seed"                                                          # step 5
pub=$(cat id_ed25519.pub) || die "no public key"
: >seed/user-data
while IFS= read -r line || [ -n "$line" ]; do
  case $line in
    hostname:*) line="hostname: $TREX_VM_HOSTNAME" ;;
    *"<contents of "*) line="      - $pub" ;;
  esac
  printf '%s\n' "$line" >>seed/user-data
done <"$HERE/seed/user-data.template"
printf 'instance-id: %s\nlocal-hostname: %s\n' "$TREX_VM_INSTANCE_ID" "$TREX_VM_HOSTNAME" >seed/meta-data
xorriso -as mkisofs -output seed.iso -volid cidata -joliet -rock seed/user-data seed/meta-data || die "xorriso failed"
qemu-img create -f qcow2 -b "$TREX_VM_DL/$TREX_VM_IMAGE" -F qcow2 trex.qcow2 12G || die "qemu-img failed"   # step 6

trap cleanup EXIT                                                                               # step 7
t_cold=$(now)
launch_and_wait_ssh || die "no ssh within 180 s (see $W/console.log, $W/build-qemu.log)"
t_ssh=$(now)
"$HERE/ssh.sh" "cloud-init status --wait" </dev/null; rc=$?
case $rc in
  0) ;;
  2) echo "build.sh: cloud-init status exited 2 (recoverable errors); continuing" >&2 ;;
  *) die "cloud-init status --wait exited $rc" ;;
esac

(cd "$TREX_VM_DL_TREX" && sha256sum -c "$HERE/v3.08.tar.gz.sha256") || die "v3.08.tar.gz hash"       # step 8
for f in "$TREX_VM_DL_TREX/v3.08.tar.gz" "$HERE/guest/trex_cfg.yaml" "$HERE/guest/setup.sh"; do
  "$HERE/ssh.sh" "cat > $(basename "$f")" <"$f" || die "could not copy $(basename "$f") into the guest"
done
"$HERE/ssh.sh" "sudo bash ./setup.sh" </dev/null || die "setup.sh failed in the guest"

t0file=$(mktemp) || die "mktemp failed"                                                               # step 9
ready() { (cd "$REPO" && uv run --project backend python -I -B vm/ready.py "$t0file"); }
t_trex=$(now)
"$HERE/ssh.sh" "sudo sh -c 'cd /opt/trex/v3.08 && setsid nohup ./t-rex-64 -i --iom 0 --no-scapy-server --cfg /etc/trex_cfg.yaml >/tmp/trex.log 2>&1 </dev/null &'" </dev/null \
  || die "could not start TRex"
date +%s.%N >"$t0file"
ready || die "TRex did not answer (guest log /tmp/trex.log)"
t_ready=$(now)

if mon "info snapshots" | grep -q trex_ready; then die "$W already has a trex_ready snapshot: refusing to overwrite"; fi   # step 10
t_save=$(now)
mon "savevm trex_ready" 120 >/dev/null
t_saved=$(now)
mon "info snapshots" | grep -q trex_ready || die "savevm did not create trex_ready"

mon quit >/dev/null; wait_gone || die "QEMU (pid $QPID) did not exit within 30 s"                    # step 11
QPID=""
t_load=$(now)
date +%s.%N >"$t0file"
launch_and_wait_ssh trex_ready || die "no ssh after loadvm (see $W/build-qemu.log)"
"$HERE/ssh.sh" "sudo date -u -s @$(date +%s.%N)" </dev/null >/dev/null || die "could not step the guest clock"
ready || die "TRex did not answer after loadvm"
t_loaded=$(now)
mon quit >/dev/null; wait_gone || die "QEMU (pid $QPID) did not exit within 30 s"
rm -f "$t0file"

echo "built $guest in $W"                                                                             # step 12
echo "cold boot to ssh: $(elapsed "$t_cold" "$t_ssh") s"
echo "TRex start to RPC ready: $(elapsed "$t_trex" "$t_ready") s"
echo "savevm: $(elapsed "$t_save" "$t_saved") s"
echo "loadvm to RPC ready: $(elapsed "$t_load" "$t_loaded") s"
