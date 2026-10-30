#!/bin/bash
# Usage: launch.sh [loadvm-tag]. Boots the TRex guest under a 6 GiB cgroup cap. The guest comes from
# TREX_VM_GUEST (vm/guests.sh; default jammy). Forwards (127.0.0.1 only), host port -> guest port:
# ssh -> 22, TRex PUB/RPC -> 4500/4501, EMU -> 4510, daemon -> 8090; jammy uses 2222/4500/4501/4510/8090.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/guests.sh" || exit 2
W=$TREX_VM_DIR
extra=()
[ $# -ge 1 ] && extra=(-loadvm "$1")
fwd=hostfwd=tcp:127.0.0.1
exec systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 -- \
  qemu-system-x86_64 -name "$TREX_VM_NAME" -enable-kvm -cpu host -smp 4 -m 4096 \
  -drive file=$W/trex.qcow2,if=virtio,format=qcow2 \
  -drive file=$W/seed.iso,if=virtio,format=raw,readonly=on \
  -nic user,model=virtio-net-pci,$fwd:$TREX_VM_SSH_PORT-:22,$fwd:$TREX_REAL_ASYNC_PORT-:4500,$fwd:$TREX_REAL_SYNC_PORT-:4501,$fwd:$TREX_VM_EMU_PORT-:4510,$fwd:$TREX_VM_DAEMON_PORT-:8090 \
  -display none -serial file:$W/console.log \
  -monitor unix:$W/monitor.sock,server,nowait -pidfile $W/qemu.pid \
  "${extra[@]}"
