# The test VM: software-mode TRex v3.08 in a KVM guest

The `real` test tier and the fixture recorders run against TRex v3.08 in an Ubuntu KVM guest, jammy (22.04)
unless `TREX_VM_GUEST` names noble (24.04) or resolute (26.04) (the table below), with
two veth ports (`vm/guest/trex_cfg.yaml`; no `stack` key, and the server reports `legacy`). This design was measured in P0 spike 2
(2026-10-16; results in the maintainer's private planning records).

## Host prerequisites

`qemu-system-x86_64` with KVM, `qemu-img`, `socat` (the QEMU monitor), `xorriso` (the cloud-init seed),
`gpgv` and the `ubuntu-cloudimage-keyring` (`download.sh`), `curl`, `ssh`, `systemd-run --user` (cgroup
caps), and `uv`.

## Working directory

The guest is picked by `TREX_VM_GUEST` (default `jammy`; the Makefile exports it). `vm/guests.sh` turns that
into the working directory `$TREX_VM_DIR` and the ports, and `launch.sh`, `ssh.sh`, `real.sh`, `demo.sh`, `stack.sh` (and `download.sh`, `build.sh`) source it; `netem.sh` reaches the guest through `ssh.sh`. Precedence: `TREX_VM_GUEST`
selects the row; a `TREX_VM_DIR` set by hand overrides the row's directory (with the guest named, a warning says
so once; with no guest, jammy's behaviour is exactly as before). The directory holds the images, the SSH key, the
monitor socket and the console log, and is not in git. Keep it on a disk that survives a reboot (`/var/tmp`, not a
tmpfs `/tmp`).

| Guest | Directory | ssh | TRex async / sync (RPC) | EMU | daemon | demo GUI |
|---|---|---|---|---|---|---|
| `jammy` (22.04) | `/var/tmp/trex-vm` | 2222 | 4500 / 4501 | 4510 | 8090 | 8765 |
| `resolute` (26.04) | `/var/tmp/trex-vm-resolute` | 2322 | 4600 / 4601 | 4610 | 8190 | 8865 |
| `noble` (24.04) | `/var/tmp/trex-vm-noble` | 2422 | 4700 / 4701 | 4710 | 8290 | 8965 |

The scripts enforce one user per guest. Run one VM at a time unless you mean to run two.

## Using it

| Command | What it does |
|---|---|
| `make check-real` | `vm/real.sh`: restores the `trex_ready` snapshot, steps the guest clock, runs `pytest -m real`, always shuts the VM down |
| `make record-p2` … `make record-p3b` | the same, but runs `vm/record_<phase>.py` into `tests/fixtures/<phase>/` (`TREX_VM_RECORD=p2\|p3\|p3a\|p3b`) |
| `TREX_VM_SNAPSHOT=<tag> make check-real` | restore another snapshot instead of `trex_ready` |
| `make check-daemon-real` | `vm/real.sh` with `TREX_VM_MARK=restarts_trex` (jammy only): the real tests that restart the guest's TRex; `make check-real` runs `real and not restarts_trex` |
| `make record-p5` | the monitor and daemon fixtures (`TREX_VM_RECORD=p5`, jammy only, one VM user): afterwards `real.sh` runs `vm/daemon.sh stop` and `vm/mode.sh stateless` and keeps the recorder's rc |
| `make record-p5-stf` | a daemon-started `-f` run, the server file listings and the upload suitability gate (`TREX_VM_RECORD=p5-stf` into `tests/fixtures/p5-stf`, jammy only, one VM user): afterwards `real.sh` runs `vm/daemon.sh stop` and `vm/mode.sh stateless` and keeps the recorder's rc; the guest is never saved, so pushed files are gone at the next boot |
| `vm/mode.sh stateful\|stateless\|stop` | switch the running guest's TRex: stateful (`t-rex-64 -f cap2/sfr2.yaml`, waits for a `trex-global`, `vm/pub_ready.py`) or back to legacy stateless (`vm/stack.sh legacy`); never saves the snapshot |
| `vm/daemon.sh start\|stop\|status` | run `trex_daemon_server start-live` in the guest (log `/tmp/trex-daemon.log`, pid `/tmp/trex-daemon.pid`); `stop` is TERM then KILL; `status` probes the forwarded port |
| `make record-p3b-v6` | the P3b recorder's linux_based counterpart: `real.sh` restarts the guest's TRex on `stack: linux_based` (`vm/stack.sh`), runs `vm/record_p3b_v6.py` into `tests/fixtures/p3b-v6/`, and puts it back on legacy |
| `vm/stack.sh legacy\|linux_based` | restart TRex in a running guest on that stack (`vm/guest/trex_cfg*.yaml`); the snapshot stays legacy |
| `vm/demo.sh up\|down\|status` | a manual or browser session: guest plus the GUI on `127.0.0.1:<its GUI port>` (jammy 8765), left running; `up` prints the SSH tunnel for another machine |
| `vm/demo.sh status --all` | `status` for every guest, each block headed `<codename>:` |
| `TREX_VM_GUEST=resolute make check-real` | the same on another guest (recorders refuse: fixtures are recorded from jammy only) |
| `make check-real-all` | `vm/real_all.sh`: `check-real` on every guest in turn, never stopping early; prints `check-real-all: <guest> rc=<n>` per guest and exits 1 if any failed (refuses `TREX_VM_RECORD`) |
| `vm/launch.sh trex_ready` | boot the guest and leave it running with nothing else (`demo.sh` is the usual way) |
| `vm/ssh.sh <cmd>` | run a command in the guest |

`real.sh`, the recorders and `demo.sh` refuse to start while that guest's VM runs (its pidfile or its ssh port);
the operating rule stays one VM user at a time. After `vm/launch.sh`,
step the guest clock (it lags by the time the snapshot spent saved):
`vm/ssh.sh "sudo date -u -s @$(date +%s.%N)"`. Shut down with
`printf "quit\n" | socat -t 5 - UNIX-CONNECT:$TREX_VM_DIR/monitor.sock`.

Ports are forwarded on 127.0.0.1 only: for jammy 2222→22 (ssh), 4500 (TRex PUB), 4501 (RPC), 4510 (EMU), 8090
(daemon); other guests add their offset (table above). The guest runs under a 6 GiB cgroup cap (4 vCPU, 4 GiB RAM).

## Building it

`vm/build.sh <guest> [--rebuild]` builds any guest except jammy from scratch in its own work dir, then prints four
figures (cold boot to ssh, TRex start to RPC ready, `savevm`, `loadvm` to RPC ready). `jammy` is built by hand, with
the steps below, and `build.sh jammy` always refuses. The script refuses (exit 2, before touching anything) an
unknown guest, a `TREX_VM_DIR`, `TREX_VM_DL` or `TREX_VM_SHARED` set by hand (`TREX_VM_BASE` is the only redirect),
a dir that is, or lies under, a protected dir (`/var/tmp/trex-vm`, plus each entry of `TREX_VM_PROTECT_ALSO`,
compared by `realpath -m`), a guest whose QEMU or ports are in use, and an existing `trex.qcow2` unless `--rebuild`
is given. `--rebuild` removes only `trex.qcow2`, `seed.iso`, `seed/user-data`, `seed/meta-data`, `qemu.pid`,
`console.log` and `known_hosts`, and keeps the SSH key pair. Other guests keep the image in
`/var/tmp/trex-vm-dl/<guest>/` and share one `v3.08.tar.gz` and `tls/bundle.pem` in `/var/tmp/trex-vm-dl/`; the
bundle is copied from jammy's `tls/` when absent. Run one VM user at a time.

What it does, and what jammy's by-hand build was:

1. `vm/download.sh`: fetches the guest's Ubuntu cloud image, `SHA256SUMS` and `SHA256SUMS.gpg`, and TRex
   `v3.08.tar.gz`. For jammy, built by hand here, all of it goes into `$TREX_VM_DIR/dl` (22.04). `vm/build.sh <guest>`
   builds the other guests: images go to `/var/tmp/trex-vm-dl/<codename>/` and the one shared tarball to
   `/var/tmp/trex-vm-dl/` (`$TREX_VM_DL`, `$TREX_VM_DL_TREX`); those dirs are made mode 700, and refused when they
   are symlinks, not yours, or group- or world-writable. It verifies `SHA256SUMS` with `gpgv` against
   `/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg`, then the image against it, then TRex against the
   committed `v3.08.tar.gz.sha256`, and exits non-zero on any failure. `--verify-only` re-checks the files
   already in `dl/`.
   - trex-tgn.cisco.com leaves its intermediate certificate out of the TLS chain, so `tls/bundle.pem` is the
     system CA bundle plus HydrantID Server CA O1, fetched from the certificate's AIA URL and checked with
     `openssl verify` against the system roots. Verification is never disabled.
2. Seed and disk, in `$TREX_VM_DIR`:
   - `ssh-keygen -t ed25519 -N '' -f id_ed25519`;
   - fill in `vm/seed/user-data.template` as `seed/user-data`;
   - `xorriso -as mkisofs -output seed.iso -volid cidata -joliet -rock seed/user-data seed/meta-data`;
   - `qemu-img create -f qcow2 -b dl/ubuntu-22.04-server-cloudimg-amd64.img -F qcow2 trex.qcow2 12G`.
3. `vm/launch.sh` (no snapshot: a cold boot).
4. Copy `v3.08.tar.gz`, `vm/guest/trex_cfg.yaml` and `vm/guest/setup.sh` into the guest and run
   `sudo bash ./setup.sh`: it creates the veth pair, extracts TRex and installs the config.
5. In the guest, from `/opt/trex/v3.08`, start TRex:
   `sudo nohup ./t-rex-64 -i --iom 0 --no-scapy-server --cfg /etc/trex_cfg.yaml > /tmp/trex.log 2>&1 &`
6. Snapshot: `printf 'savevm trex_ready\n' | socat - UNIX-CONNECT:$TREX_VM_DIR/monitor.sock`. Check readiness
   with `uv run --project backend python vm/ready.py <t0-file>` (it imports the vendored client).
