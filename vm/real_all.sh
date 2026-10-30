#!/bin/bash
# make check-real-all: runs vm/real.sh once per guest in vm/guests.sh's table, in order, each with the per-guest
# environment cleared. It never stops early; it prints `check-real-all: <guest> rc=<n>` after each guest and exits 1
# if any guest failed (an unbuilt guest, real.sh rc 2, counts), else 0. TREX_VM_STACK, TREX_VM_BASE and the rest pass
# through. TREX_REAL_SH replaces real.sh (the test seam). Takes no arguments; refuses TREX_VM_RECORD.
set -u
[ $# -eq 0 ] || { echo "usage: real_all.sh (no arguments)" >&2; exit 2; }
HERE=$(cd "$(dirname "$0")" && pwd)
if [ -n "${TREX_VM_RECORD:-}" ]; then
  echo "check-real-all runs the tests, not a recorder" >&2; exit 2
fi
CLEARED=(TREX_VM_DIR TREX_VM_DL TREX_VM_SHARED TREX_VM_CODENAME TREX_VM_DIR_WARNED TREX_VM_SNAPSHOT TREX_VM_MARK)
set_names=()
for v in "${CLEARED[@]}"; do [ -n "${!v:-}" ] && set_names+=("$v"); done
[ ${#set_names[@]} -gt 0 ] && echo "check-real-all: ignoring ${set_names[*]} for per-guest runs"
unset_args=(); for v in "${CLEARED[@]}"; do unset_args+=(-u "$v"); done
guests=$("$HERE/guests.sh" --list) || exit 2
fail=0
for g in $guests; do
  env "${unset_args[@]}" TREX_VM_GUEST="$g" "${TREX_REAL_SH:-$HERE/real.sh}"
  rc=$?
  echo "check-real-all: $g rc=$rc"
  [ "$rc" -ne 0 ] && fail=1
done
exit $fail
