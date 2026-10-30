#!/bin/bash
# Runs inside the guest as root: veth pair for TRex data plane, extract TRex, install cfg.
set -eux
ip link show veth0 >/dev/null 2>&1 || ip link add veth0 type veth peer name veth1
ip link set veth0 up; ip link set veth1 up
[ -d /opt/trex/v3.08 ] || { mkdir -p /opt/trex; tar -xzf /home/trex/v3.08.tar.gz -C /opt/trex; }
install -m 644 /home/trex/trex_cfg.yaml /etc/trex_cfg.yaml
ls /opt/trex/v3.08 | head -5; nproc; free -m | head -2; uname -r
