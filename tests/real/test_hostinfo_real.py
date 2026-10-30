"""P3b follow-up 6 against the real v3.08 guest (make check-real): the interface names resolve from the
guest's own files (fetched over vm/ssh.sh into a tmp tree), and a backend on a different machine than TRex
looks nothing up.
Only the af_packet guest runs here; DPDK/vfio and mlx5 are fixture-only (UNVERIFIED on hardware)."""
import asyncio
import os
import shlex
import socket
import subprocess
from pathlib import Path

import pytest
from support import ROOT, real_ports, real_vm_dir
from trex_web_gui.hostinfo import parse_interfaces, resolve
from trex_web_gui.sessions import ConnectParams, TrexSession

pytestmark = pytest.mark.real
HOST = os.environ.get("TREX_REAL_HOST", "")
SSH = ROOT / "vm" / "ssh.sh"


@pytest.fixture(autouse=True)
def need_ssh() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST is set and the VM's ssh key exists."""
    if not HOST:
        pytest.skip("run via make check-real")
    real_ports()
    key = real_vm_dir() / "id_ed25519"
    if not key.exists():
        pytest.skip(f"no ssh key at {key}")


def guest(cmd: str) -> str:
    """Run `cmd` in the guest over vm/ssh.sh and return its stdout (read-only commands only)."""
    return subprocess.run([str(SSH), cmd], check=True, capture_output=True, text=True, timeout=30).stdout


def fetch_guest_tree(tmp_path: Path) -> tuple[Path, Path, str]:
    """(root, cfg copy, guest hostname): the guest's cfg and, for every interface under its /sys/class/net,
    the `address` and an empty mirror of each of `device`, `lower_*`, `bonding`, `bridge`, `brport`,
    `bonding_slave`."""
    cfg = tmp_path / "trex_cfg.yaml"
    cfg.write_text(guest("cat /etc/trex_cfg.yaml"))
    hostname = guest("hostname").strip()
    root = tmp_path / "root"
    entries, _ = parse_interfaces(cfg.read_text())
    assert entries, "the guest cfg lists no interfaces"
    for name in guest("ls /sys/class/net").split():
        q = shlex.quote(name)
        address = guest(f"cat /sys/class/net/{q}/address 2>/dev/null; true")
        if not address.strip():  # a plain file such as bonding_masters, not an interface
            continue
        d = root / "sys" / "class" / "net" / name
        d.mkdir(parents=True)
        (d / "address").write_text(address)
        for present in guest(f"cd /sys/class/net/{q} && ls -d device lower_* bonding bridge brport "
                             "bonding_slave 2>/dev/null; true").split():
            (d / present).mkdir()
    return root, cfg, hostname


async def connected_ports() -> tuple[list[dict], str]:
    """Connect a session to HOST (the guest's ports, 3 s) and return copies of its port dicts and the
    server hostname; the session is closed before it returns."""
    s = TrexSession(lambda m: None)
    try:
        info = await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
        return [dict(p) for p in s.ports()], info.hostname
    finally:
        await s.close()


@pytest.mark.timeout(120)
def test_guest_interfaces_resolve(tmp_path: Path) -> None:
    root, cfg, guest_name = fetch_guest_tree(tmp_path)
    ports, server_hostname = asyncio.run(connected_ports())
    ifaces, note = resolve(cfg.read_text(), None, ports, server_hostname=server_hostname,
                           local_hostname=guest_name, root=root)
    print(f"ifaces={ifaces} note={note}")
    assert [(i.port, i.kernel_name, i.confirmed) for i in ifaces] == [(0, "veth0", True), (1, "veth1", True)]
    assert note is None


@pytest.mark.timeout(120)
def test_session_on_the_host_blocks_lookups(tmp_path: Path) -> None:
    """Review Focus 1 end to end: loopback to a TRex on another machine; the host gate withholds every
    lookup."""
    root, cfg, guest_name = fetch_guest_tree(tmp_path)
    if socket.gethostname() == guest_name:
        pytest.skip("the backend host has the guest's hostname; the gate cannot tell them apart")

    async def run() -> dict:
        s = TrexSession(lambda m: None, trex_cfg_path=lambda: str(cfg), hostinfo_root=root)
        try:
            await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            return s.snapshot()
        finally:
            await s.close()
    snap = asyncio.run(run())
    print(f"ifaces={snap['ifaces']} note={snap['ifaces_note']}")
    assert len(snap["ifaces"]) == 2
    # the root holds the guest's veth files and would confirm both names: only the gate can hide them
    assert all(i["cfg_entry"] and i["kernel_name"] is None and i["confirmed"] is False
               for i in snap["ifaces"])
    assert guest_name in snap["ifaces_note"] and "no local lookups" in snap["ifaces_note"]


@pytest.mark.timeout(120)
def test_guest_names_found_by_mac_without_cfg(tmp_path: Path) -> None:
    root, _, guest_name = fetch_guest_tree(tmp_path)
    ports, server_hostname = asyncio.run(connected_ports())
    ifaces, _ = resolve(None, None, ports, server_hostname=server_hostname, local_hostname=guest_name,
                        root=root)
    print(f"ifaces={ifaces}")
    assert [(i.port, i.kernel_name, i.confirmed, i.note) for i in ifaces] == [
        (0, "veth0", True, "found by MAC (no config entry)"),
        (1, "veth1", True, "found by MAC (no config entry)")]


@pytest.mark.timeout(120)
def test_swapped_cfg_names_the_mac_carrier(tmp_path: Path) -> None:
    root, _, guest_name = fetch_guest_tree(tmp_path)
    ports, server_hostname = asyncio.run(connected_ports())
    ifaces, _ = resolve("- interfaces: [veth1, veth0]\n", None, ports, server_hostname=server_hostname,
                        local_hostname=guest_name, root=root)
    print(f"ifaces={ifaces}")
    by_port = {i.port: i for i in ifaces}
    assert (by_port[0].kernel_name, by_port[0].confirmed) == (None, False)
    assert (by_port[1].kernel_name, by_port[1].confirmed) == (None, False)
    assert (by_port[0].note and by_port[0].note.startswith("veth1 not confirmed: MAC ")
            and by_port[0].note.endswith("is on veth0"))
    assert (by_port[1].note and by_port[1].note.startswith("veth0 not confirmed: MAC ")
            and by_port[1].note.endswith("is on veth1"))
