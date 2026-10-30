#!/bin/bash
# Selects the TRex test guest from TREX_VM_GUEST (empty or unset: jammy). Sourced by the vm/ scripts
# (. "$HERE/guests.sh" || exit 2), it exports the selected row's names, dirs and ports. Executed, it prints
# `export KEY=value` lines for eval, ignoring any inherited per-guest variables; `--list` prints the row names.
# Rows: name|Ubuntu release|port offset|work-dir suffix. Add a guest by adding a row.
_TREX_GUEST_ROWS=(
  "jammy|22.04|0|"
  "resolute|26.04|100|-resolute"
  "noble|24.04|200|-noble"
)

# Prints the row names, one per line, in table order.
_trex_guest_names() {
  local row
  for row in "${_TREX_GUEST_ROWS[@]}"; do echo "${row%%|*}"; done
}

# _trex_guest_select: resolves TREX_VM_GUEST against the table and exports the row's keys. Returns 2 on refusal.
_trex_guest_select() {
  local want=${TREX_VM_GUEST:-jammy} row name="" rel="" off="" suf="" given_dir=${TREX_VM_DIR:-} names=""
  for row in "${_TREX_GUEST_ROWS[@]}"; do
    IFS='|' read -r name rel off suf <<<"$row"
    names+=${names:+, }$name
    [ "$name" = "$want" ] && break
    name=""
  done
  if [ -z "$name" ]; then
    echo "guests.sh: unknown TREX_VM_GUEST '$want' ($names)" >&2
    return 2
  fi
  if [ -n "${TREX_VM_CODENAME:-}" ] && [ "$TREX_VM_CODENAME" != "$name" ]; then
    echo "guests.sh: this environment was prepared for $TREX_VM_CODENAME (eval'd guests.sh?), not $name;" \
      "unset TREX_VM_CODENAME TREX_VM_DIR TREX_VM_DL TREX_VM_SHARED" >&2
    return 2
  fi
  local base=${TREX_VM_BASE:-/var/tmp} row_dir
  row_dir=$base/trex-vm$suf
  if [ -n "${TREX_VM_GUEST:-}" ] && [ -n "$given_dir" ] && [ "$given_dir" != "$row_dir" ] \
    && [ -z "${TREX_VM_DIR_WARNED:-}" ]; then
    echo "guests.sh: TREX_VM_DIR=$given_dir overrides the $name work dir $row_dir" >&2
    export TREX_VM_DIR_WARNED=1
  fi
  export TREX_VM_CODENAME=$name
  export TREX_VM_DIR=${TREX_VM_DIR:-$row_dir}
  export TREX_VM_NAME=trex-vm$suf TREX_VM_HOSTNAME=trex-vm$suf TREX_VM_INSTANCE_ID=trex-vm$suf-1
  export TREX_VM_IMAGE=ubuntu-$rel-server-cloudimg-amd64.img
  export TREX_VM_IMAGE_URL=https://cloud-images.ubuntu.com/releases/$name/release
  export TREX_VM_SHARED=${TREX_VM_SHARED:-$base/trex-vm-dl}
  if [ "$name" = jammy ]; then
    export TREX_VM_DL=${TREX_VM_DL:-$TREX_VM_DIR/dl}
    export TREX_VM_DL_TREX=$TREX_VM_DL
    export TREX_VM_TLS=$TREX_VM_DIR/tls/bundle.pem
  else
    export TREX_VM_DL=${TREX_VM_DL:-$TREX_VM_SHARED/$name}
    export TREX_VM_DL_TREX=$TREX_VM_SHARED
    export TREX_VM_TLS=$TREX_VM_SHARED/tls/bundle.pem
  fi
  export TREX_VM_SSH_PORT=$((2222 + off)) TREX_REAL_ASYNC_PORT=$((4500 + off)) TREX_REAL_SYNC_PORT=$((4501 + off))
  export TREX_VM_EMU_PORT=$((4510 + off)) TREX_VM_DAEMON_PORT=$((8090 + off)) TREX_VM_GUI_PORT=$((8765 + off))
}

# trex_vm_running: 0 iff $TREX_VM_DIR/qemu.pid names a live process that is this guest's QEMU: its argv has
# `-name $TREX_VM_NAME`, or a `file=$TREX_VM_DIR/trex.qcow2,` drive (this dir's disk under another name).
trex_vm_running() {
  local pid arg prev="" argv=()
  { read -r pid <"$TREX_VM_DIR/qemu.pid"; } 2>/dev/null || true
  [[ ${pid:-} =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  { mapfile -d '' argv <"/proc/$pid/cmdline"; } 2>/dev/null || return 1
  for arg in "${argv[@]}"; do
    [ "$prev" = -name ] && [ "$arg" = "$TREX_VM_NAME" ] && return 0
    case $arg in "file=$TREX_VM_DIR/trex.qcow2,"*) return 0 ;; esac
    prev=$arg
  done
  return 1
}

# trex_port_open <port>: 0 iff something accepts a TCP connection on 127.0.0.1:<port>.
trex_port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# trex_dir_unsafe <dir>: prints why and returns 0 iff <dir> exists and is a symlink, is not owned by the current
# uid, or is group- or world-writable (the dirs live under world-writable /var/tmp). A missing dir is not unsafe.
trex_dir_unsafe() {
  local d=$1 owner mode
  [ -e "$d" ] || [ -L "$d" ] || return 1
  if [ -L "$d" ]; then echo "$d is a symlink"; return 0; fi
  read -r owner mode < <(stat -c '%u %a' -- "$d") || { echo "cannot stat $d"; return 0; }
  if [ "$owner" != "$(id -u)" ]; then echo "$d is not owned by the current user"; return 0; fi
  if (( (8#$mode & 8#022) != 0 )); then echo "$d is group- or world-writable (mode $mode)"; return 0; fi
  return 1
}

# trex_mkdir700 <dir>: makes <dir> and any missing parents with mode 700, then verifies (trex_dir_unsafe) the leaf
# and every component it had to make, so a dir another user slipped in between a check and the mkdir is refused.
# Returns nonzero, printing why, for a leaf that is a symlink, not yours, or group- or world-writable (its mode is
# left alone). Parents that already existed (/var/tmp) are not examined.
trex_mkdir700() {
  local d=$1 fresh=0 why
  if [ ! -e "$d" ] && [ ! -L "$d" ]; then
    fresh=1
    trex_mkdir700 "$(dirname "$d")" parent || return 1
    mkdir -m 700 -- "$d" 2>/dev/null || true   # losing a race is caught by the check below
  fi
  if [ -z "${2:-}" ] || [ $fresh = 1 ]; then
    if why=$(trex_dir_unsafe "$d"); then echo "$why" >&2; return 1; fi
    [ -d "$d" ] || { echo "$d is not a directory" >&2; return 1; }
  fi
  return 0
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  if [ "${1:-}" = --list ] && [ $# -eq 1 ]; then _trex_guest_names; exit 0; fi
  if [ $# -ne 0 ]; then echo "usage: guests.sh [--list]" >&2; exit 2; fi
  unset TREX_VM_DIR TREX_VM_DL TREX_VM_SHARED TREX_VM_CODENAME
  _trex_guest_select || exit 2
  printf 'export TREX_VM_GUEST=%q\n' "$TREX_VM_CODENAME"
  for _k in CODENAME DIR NAME HOSTNAME INSTANCE_ID IMAGE IMAGE_URL SHARED DL DL_TREX TLS SSH_PORT EMU_PORT \
    DAEMON_PORT GUI_PORT; do
    _n=TREX_VM_$_k; printf 'export %s=%q\n' "$_n" "${!_n}"
  done
  printf 'export TREX_REAL_ASYNC_PORT=%q\nexport TREX_REAL_SYNC_PORT=%q\n' "$TREX_REAL_ASYNC_PORT" "$TREX_REAL_SYNC_PORT"
else
  _trex_guest_select
fi
