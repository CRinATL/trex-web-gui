#!/bin/bash
# Usage: vm/netem.sh <delay_ms> <jitter_ms> | off
# Put a netem delay on the guest NIC (ens3) over vm/ssh.sh, or take it off, then print `tc qdisc show dev ens3`.
# Integers with 0 <= J <= D <= 1000; exit 2 on anything else, before any ssh. `replace`, not `add`, so a second
# delay never stacks on the first. `0 0` is the same as `off`. NETEM_SSH replaces vm/ssh.sh (the unit tests stub it).
# Nothing outlives a run: every boot is -loadvm trex_ready (vm/launch.sh), which restores device state.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SSH=${NETEM_SSH:-$HERE/ssh.sh}
usage() { sed -n '2,3p' "$0" >&2; exit 2; }
off() { "$SSH" "sudo tc qdisc del dev ens3 root 2>/dev/null; true" </dev/null || exit 1; }
if [ $# -eq 1 ] && [ "$1" = off ]; then
  off
elif [ $# -eq 2 ]; then
  [[ $1 =~ ^[0-9]+$ && $2 =~ ^[0-9]+$ ]] || usage
  d=$((10#$1)); j=$((10#$2))
  [ "$j" -le "$d" ] && [ "$d" -le 1000 ] || usage
  if [ "$d" -eq 0 ]; then  # 0 0 is `off`: no netem qdisc at all
    off
  else
    "$SSH" "sudo modprobe sch_netem; sudo tc qdisc replace dev ens3 root netem delay ${d}ms ${j}ms" </dev/null || exit 1
  fi
else
  usage
fi
"$SSH" "tc qdisc show dev ens3" </dev/null
