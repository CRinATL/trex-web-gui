#!/bin/bash
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/guests.sh" || exit 2
W=$TREX_VM_DIR
exec ssh -i $W/id_ed25519 -p $TREX_VM_SSH_PORT -o StrictHostKeyChecking=no -o UserKnownHostsFile=$W/known_hosts -o ConnectTimeout=5 -o LogLevel=ERROR trex@127.0.0.1 "$@"
