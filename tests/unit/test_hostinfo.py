"""The pure hostinfo resolver. Every test reads fixture trees under tests/fixtures/hostinfo and stubs lspci:
the host's real /sys and the real `lspci` are never touched (make check-py runs this on 3.12-3.14)."""
import os
import pwd
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from trex_web_gui import hostinfo
from trex_web_gui.hostinfo import (
    PortIface,
    lspci_models,
    parse_interfaces,
    read_cfg,
    resolve,
)

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "hostinfo"
VETH_MACS = ("8e:4e:97:11:bf:a8", "52:52:18:89:91:ea")     # the guest's, from tests/fixtures/p3b/rpc.jsonl


def port(pid: int, mac: str | None = None, pci: str = "N/A") -> dict[str, Any]:
    """An RPC port entry: id, plus info with pci_addr and, when mac is given, hw_mac."""
    info: dict[str, Any] = {"pci_addr": pci}
    if mac is not None:
        info["hw_mac"] = mac
    return {"id": pid, "info": info}


def cfg_of(scenario: str) -> str:
    """The trex_cfg.yaml text of a fixture scenario under the hostinfo fixtures."""
    return (FIX / scenario / "etc" / "trex_cfg.yaml").read_text()


class Stub:
    def __init__(self, models: dict[str, str] | None = None) -> None:
        """Starts with a zero call count; models is the lspci result the stub returns."""
        self.calls = 0
        self.models = models or {}

    def __call__(self) -> dict[str, str]:
        """The lspci stand-in: counts the call, returns the models dict."""
        self.calls += 1
        return self.models


def run(scenario: str, cfg: str | None, ports: list[dict[str, Any]], stub: Stub | None = None,
        server: str = "h", local: str = "h",
        cfg_note: str | None = None) -> tuple[list[PortIface], str | None]:
    """resolve() over the fixture scenario's sysfs tree with the given hostnames and a counting lspci stub."""
    return resolve(cfg, cfg_note, ports, server_hostname=server, local_hostname=local, root=FIX / scenario,
                   lspci=stub or Stub())


def test_af_packet_names_confirmed_by_mac() -> None:
    ifaces, note = run("af_packet_veth", cfg_of("af_packet_veth"),
                       [port(0, VETH_MACS[0]), port(1, VETH_MACS[1])])
    assert [(i.port, i.cfg_entry, i.kind, i.kernel_name, i.confirmed, i.note) for i in ifaces] == [
        (0, "veth0", "af_packet", "veth0", True, None), (1, "veth1", "af_packet", "veth1", True, None)]
    assert note is None


def test_af_packet_wrong_mac_is_not_confirmed() -> None:
    ifaces, _ = run("af_packet_veth", cfg_of("af_packet_veth"),
                    [port(0, VETH_MACS[1]), port(1, VETH_MACS[1])])
    assert ifaces[0].kernel_name is None and not ifaces[0].confirmed and ifaces[0].cfg_entry == "veth0"
    assert ifaces[0].note and "veth0" in ifaces[0].note
    assert ifaces[1].kernel_name == "veth1"


def test_af_packet_traversal_name_is_refused(tmp_path: Path) -> None:
    ifaces, _ = resolve("- interfaces: ['../x', 'a/b']\n", None,
                        [port(0, "aa:bb:cc:dd:ee:01"), port(1, "aa:bb:cc:dd:ee:02")],
                        server_hostname="h", local_hostname="h", root=tmp_path, lspci=Stub())
    assert [i.kernel_name for i in ifaces] == [None, None] and all(i.note for i in ifaces)


def test_host_gate_blocks_all_lookups() -> None:
    stub = Stub({"0000:03:00.0": "x y"})
    ifaces, note = run("af_packet_veth", cfg_of("af_packet_veth"),
                       [port(0, VETH_MACS[0]), port(1, VETH_MACS[1])],
                       stub, server="trex-vm", local="devbox")
    assert note == "TRex reports host trex-vm; this backend runs on devbox: no local lookups"
    for i in ifaces:
        assert i.kernel_name is None and i.driver is None and i.model is None and i.vendor_device is None
        assert not i.confirmed and i.kind == "af_packet"
    ifaces, _ = run("dpdk_vfio", None, [port(0, "aa:aa:aa:aa:aa:aa", "0000:03:00.0")], stub,
                    server="a", local="b")
    assert ifaces[0].driver is None and ifaces[0].model is None and not ifaces[0].confirmed
    assert stub.calls == 0


def test_host_gate_touches_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ports = [port(0, VETH_MACS[0]), port(1, VETH_MACS[1], "0000:03:00.0")]
    cfg = "- interfaces: [veth0, '03:00.1']\n"
    stub = Stub()
    with_tree = run("af_packet_veth", cfg, ports, stub, server="a", local="b")
    absent = tmp_path / "does-not-exist"
    no_tree = resolve(cfg, None, ports, server_hostname="a", local_hostname="b", root=absent, lspci=stub)
    assert with_tree == no_tree and stub.calls == 0

    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("filesystem access behind the host gate")

    for target in ("listdir", "open", "stat"):
        monkeypatch.setattr(os, target, boom)
    monkeypatch.setattr(Path, "read_text", boom)
    monkeypatch.setattr(Path, "exists", boom)
    monkeypatch.setattr(Path, "is_dir", boom)
    monkeypatch.setattr("builtins.open", boom)
    assert resolve(cfg, None, ports, server_hostname="a", local_hostname="b", root=FIX / "af_packet_veth",
                   lspci=stub) == with_tree


@pytest.mark.parametrize("text, expected", [
    ("- interfaces: [03:00.0, 03:00.1]\n", ["03:00.0", "03:00.1"]),
    ("- interfaces: ['03:00.0', \"03:00.1\"]\n", ["03:00.0", "03:00.1"]),
    ("- interfaces: [0000:03:00.0, 0000:03:00.1]\n", ["0000:03:00.0", "0000:03:00.1"]),
    ("- interfaces: [03:00.0, veth0]\n", ["03:00.0", "veth0"]),
    ("- interfaces: [on, yes, no, null, ~, off, true, 180, 1.5]\n",
     ["on", "yes", "no", "null", "~", "off", "true", "180", "1.5"]),
])
def test_unquoted_pci_entries_stay_strings(text: str, expected: list[str]) -> None:
    assert parse_interfaces(text)[0] == expected


def test_parse_interfaces_port_limit_and_errors() -> None:
    assert parse_interfaces("- port_limit: 2\n  interfaces: [a, b, c, d]\n") == (["a", "b", "c", "d"], 2)
    for bad in ("interfaces: [a]\n", "- version: 2\n", "[unclosed", "- 3\n", "", "- interfaces: abc\n"):
        with pytest.raises(ValueError):
            parse_interfaces(bad)


def test_count_mismatch_attaches_no_cfg_entry() -> None:
    ifaces, note = run("af_packet_veth", "- interfaces: [veth0, veth1, veth2]\n",
                       [port(0, VETH_MACS[0]), port(1, VETH_MACS[1])])
    assert all(i.cfg_entry is None and i.kind is None for i in ifaces)
    assert note and "3" in note and "2" in note


def test_port_limit_clamps_the_list() -> None:
    ifaces, note = run("af_packet_veth", "- port_limit: 2\n  interfaces: [veth0, veth1, x, y]\n",
                       [port(0, VETH_MACS[0]), port(1, VETH_MACS[1])])
    assert [i.cfg_entry for i in ifaces] == ["veth0", "veth1"] and note is None


def test_ports_in_any_order_index_by_id() -> None:
    ifaces, _ = run("af_packet_veth", cfg_of("af_packet_veth"),
                    [port(1, VETH_MACS[1]), port(0, VETH_MACS[0])])
    assert [(i.port, i.cfg_entry, i.kernel_name) for i in ifaces] == [
        (0, "veth0", "veth0"), (1, "veth1", "veth1")]


def test_dpdk_vfio_has_model_no_name() -> None:
    stub = Stub({"0000:03:00.0": "Intel Corporation Ethernet Controller X710"})
    ifaces, note = run("dpdk_vfio", cfg_of("dpdk_vfio"),
                       [port(0, "3c:fd:fe:00:00:01", "0000:03:00.0"),
                        port(1, "3c:fd:fe:00:00:02", "0000:03:00.1")], stub)
    i = ifaces[0]
    assert (i.kind, i.cfg_entry, i.pci_addr, i.driver, i.vendor_device, i.model, i.kernel_name,
            i.confirmed) == (
        "pci", "03:00.0", "0000:03:00.0", "vfio-pci", "8086:1572",
        "Intel Corporation Ethernet Controller X710",
        None, True)
    assert i.note == "no kernel interface: bound to vfio-pci"
    assert ifaces[1].model is None and note is None


def test_mlx5_keeps_kernel_name() -> None:
    ifaces, _ = run("mlx5_bifurcated", cfg_of("mlx5_bifurcated"),
                    [port(0, "0C:42:A1:00:00:10", "0000:03:00.0"),
                     port(1, "0c:42:a1:00:00:11", "0000:03:00.1")])
    assert [(i.kernel_name, i.driver, i.vendor_device, i.note) for i in ifaces] == [
        ("ens1f0", "mlx5_core", "15b3:1017", None), ("ens1f1", "mlx5_core", "15b3:1017", None)]


def test_mac_scan_ignores_virtual_interfaces() -> None:
    # vlan100 and br0 share the MAC and have no `device`; eth9 is physical and does, so only it can match
    ifaces, _ = run("mac_shared", None, [port(0, "00:1b:21:aa:bb:cc", "0000:03:00.0")])
    assert ifaces[0].kernel_name == "eth9" and ifaces[0].driver == "e1000e" and ifaces[0].confirmed


def test_device_with_net_children_never_uses_the_system_scan() -> None:
    # 0000:04:00.0 has net/enp4s0 with another MAC; eth9 elsewhere has this port's MAC, but is not its child
    ifaces, _ = run("mac_shared", None, [port(0, "00:1b:21:aa:bb:cc", "0000:04:00.0")])
    assert ifaces[0].kernel_name is None and ifaces[0].confirmed
    assert ifaces[0].note == "no kernel interface: bound to e1000e"


def test_duplicate_pci_entries_match_after_normalising() -> None:
    ifaces, _ = run("dpdk_vfio", "- interfaces: ['03:00.0', '0000:03:00.0']\n", [port(0), port(1)])
    assert "twice" in (ifaces[1].note or "") and "twice" not in (ifaces[0].note or "")


def test_ports_without_lookups_say_why() -> None:
    ifaces, _ = run("dpdk_vfio", "- interfaces: ['03:00.0', veth0, dummy]\n", [port(0), port(1), port(2)])
    assert ifaces[0].note == "server reports no PCI address"
    assert ifaces[0].pci_addr == "0000:03:00.0"
    assert "not confirmed" in (ifaces[1].note or "") and ifaces[2].note
    ifaces, _ = run("dpdk_vfio", None, [port(0, "aa:bb:cc:dd:ee:ff")])
    assert ifaces[0].note == "no config entry and no PCI address from the server"


def test_malformed_ports_are_skipped_with_a_note() -> None:
    bad: list[Any] = [{"info": {}}, {"id": "0", "info": {}}, {"id": 1, "info": None},
                      {"id": True, "info": {}}, "x"]
    ifaces, note = run("dpdk_vfio", None, [port(0, "aa:bb:cc:dd:ee:ff", "0000:03:00.0"), *bad])
    assert [i.port for i in ifaces] == [0] and note == "skipped 5 malformed port entries"


@pytest.mark.parametrize("mac", ["00:00:00:00:00:00", "N/A", "n/a", "", None])
def test_zero_or_missing_mac_never_matches(mac: str | None) -> None:
    ifaces, _ = run("mac_shared", None, [port(0, mac, "0000:03:00.0")])
    assert ifaces[0].kernel_name is None
    ifaces, _ = run("af_packet_veth", "- interfaces: [lo]\n", [port(0, mac)])
    assert ifaces[0].kernel_name is None and not ifaces[0].confirmed


def test_no_config_still_resolves_pci_port() -> None:
    text, note = read_cfg(str(FIX / "no_cfg_pci" / "etc" / "trex_cfg.yaml"))
    assert text is None and note and note.startswith("no config file at ")
    ifaces, session = run("no_cfg_pci", text, [port(0, "00:1B:21:AA:BB:CC", "0000:03:00.0")],
                          Stub({"0000:03:00.0": "Intel Corporation I217"}), cfg_note=note)
    assert len(ifaces) == 1 and ifaces[0].cfg_entry is None and ifaces[0].kind is None
    assert (ifaces[0].kernel_name, ifaces[0].driver, ifaces[0].model, ifaces[0].confirmed) == (
        "enp3s0", "e1000e", "Intel Corporation I217", True)
    assert session == note


def test_cfg_and_server_pci_disagree() -> None:
    ifaces, _ = run("dpdk_vfio", "- interfaces: ['03:00.1']\n",
                    [port(0, "aa:aa:aa:aa:aa:aa", "0000:03:00.0")])
    assert ifaces[0].pci_addr == "0000:03:00.0" and ifaces[0].driver == "vfio-pci"
    assert ifaces[0].note and "config says 03:00.1, server says 0000:03:00.0" in ifaces[0].note


def test_pci_entry_that_is_not_an_address_gets_a_note() -> None:
    ifaces, _ = run("dpdk_vfio", "- interfaces: ['zz:zz']\n", [port(0, "aa:aa:aa:aa:aa:aa")])
    assert (ifaces[0].kind == "pci" and ifaces[0].pci_addr is None
            and "not a PCI address" in (ifaces[0].note or ""))


def test_lspci_called_once_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = Stub({"0000:03:00.0": "m"})
    ports = [port(i, f"aa:aa:aa:aa:aa:0{i}", "0000:03:00.0") for i in range(4)]
    run("dpdk_vfio", None, ports, stub)
    assert stub.calls == 1

    seen: dict[str, Any] = {}

    def boom(argv: list[str], **kw: Any) -> None:
        seen.update(argv=argv, **kw)
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(hostinfo.subprocess, "run", boom)
    assert lspci_models() == {}
    assert seen["argv"] == ["lspci", "-mm", "-D"] and seen["timeout"] == 2.0 and not seen.get("shell")
    monkeypatch.setattr(hostinfo.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert lspci_models() == {}


def test_lspci_mm_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    out = ('70:00.0 "Network controller" "Intel Corporation" "Wi-Fi 6 AX200" -r1a -p00 '
           '"Intel Corporation" "Device 4080"\n'
           '0000:03:00.0 "Ethernet controller" "Intel Corporation" "Ethernet Controller X710" -r02 -p00 '
           '"Intel" "Dev"\n'
           '0000:04:00.0 "Ethernet controller" "Mellanox" "MT27800 Family [ConnectX-5]" "Mellanox" "Dev"\n'
           'garbage "unterminated\n')

    monkeypatch.setattr(hostinfo.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    assert lspci_models() == {
        "70:00.0": "Intel Corporation Wi-Fi 6 AX200",
        "0000:03:00.0": "Intel Corporation Ethernet Controller X710",
        "0000:04:00.0": "Mellanox MT27800 Family [ConnectX-5]"}
    monkeypatch.setattr(hostinfo.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, out, ""))
    assert lspci_models() == {}


def test_dummy_vdev_duplicates_and_non_strings() -> None:
    cfg = ("- interfaces: [dummy, '--vdev=net_tap0,iface=tap0', veth0, veth0, [a], {k: v}, 03:00.0]\n")
    ifaces, note = run("af_packet_veth", cfg, [port(i, f"52:52:18:89:91:e{i}") for i in range(7)])
    assert note is None
    assert [i.kind for i in ifaces] == ["dummy", "vdev", "af_packet", "af_packet", None, None, "pci"]
    assert ifaces[1].cfg_entry == "--vdev=net_tap0,iface=tap0" and not ifaces[1].confirmed
    assert "twice" in (ifaces[3].note or "") and "twice" not in (ifaces[2].note or "")
    assert "not a string" in (ifaces[4].note or "") and "not a string" in (ifaces[5].note or "")
    assert ifaces[4].cfg_entry is None


def test_bad_cfg_is_a_session_note_not_an_exception() -> None:
    ifaces, note = run("af_packet_veth", "interfaces: [a]\n", [port(0), port(1)])
    assert len(ifaces) == 2 and all(i.cfg_entry is None for i in ifaces) and note and "map" in note


def test_read_cfg_cases(tmp_path: Path) -> None:
    missing = str(tmp_path / "nope.yaml")
    assert read_cfg(missing) == (None, f"no config file at {missing}")

    locked = tmp_path / "locked.yaml"
    locked.write_text("- interfaces: [a]\n")
    locked.chmod(0)
    if os.geteuid() != 0:
        # names the user trex-web-gui runs as and the usual remedy (RC1-16)
        user = pwd.getpwuid(os.geteuid()).pw_name
        note = (f"cannot read {locked}: Permission denied; trex-web-gui runs as {user}, who cannot read it "
                f"(for example `sudo chmod o+r {locked}`)")
        assert read_cfg(str(locked)) == (None, note)
    locked.chmod(0o600)

    assert read_cfg(str(tmp_path)) == (None, f"cannot read {tmp_path}: Is a directory")

    big = tmp_path / "big.yaml"
    big.write_bytes(b"a" * ((1 << 20) + 1))
    assert read_cfg(str(big)) == (None, f"{big} is larger than 1 MiB")
    just = tmp_path / "just.yaml"
    just.write_bytes(b"a" * (1 << 20))
    assert read_cfg(str(just))[1] is None

    bad = tmp_path / "bad.yaml"
    bad.write_bytes(b"\xff\xfe\x00bad")
    assert read_cfg(str(bad)) == (None, f"{bad} is not a text file")

    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert read_cfg(str(fifo)) == (None, f"cannot read {fifo}: not a regular file")

    ok = tmp_path / "ok.yaml"
    ok.write_text("- interfaces: [a]\n")
    assert read_cfg(str(ok)) == ("- interfaces: [a]\n", None)


def alias_bomb(levels: int, fan: int = 10) -> str:
    """A cfg whose anchored lists each repeat the previous alias fan times; the last is also port_limit."""
    lines = ["- interfaces:", "  - &a0 [x, x, x, x, x, x, x, x, x, x]"]
    for n in range(1, levels):
        lines.append(f"  - &a{n} [{', '.join([f'*a{n - 1}'] * fan)}]")
    lines.append(f"  port_limit: *a{levels - 1}")            # the same alias as a port_limit
    return "\n".join(lines) + "\n"


ALIAS_NOTE = "the config file uses YAML aliases (*name), which are not supported"


def merge_bomb(levels: int, key: str) -> str:
    """A cfg whose anchored mappings each hold ten aliases of the previous one under key ("<<" merges)."""
    lines = ["- interfaces: [veth0]", "  x0: &a0 {k: v}"]
    for n in range(1, levels):
        lines.append(f"  x{n}: &a{n} {{{key}: [*a{n - 1}, *a{n - 1}, *a{n - 1}, *a{n - 1}, *a{n - 1}, "
                     f"*a{n - 1}, *a{n - 1}, *a{n - 1}, *a{n - 1}, *a{n - 1}]}}")
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("cfg", [alias_bomb(6), alias_bomb(7), merge_bomb(9, "<<"),
                                 merge_bomb(9, "!!merge <<")],
                         ids=["list6", "list7-port-limit", "merge9", "tag-merge9"])
def test_aliases_are_refused_without_expanding(cfg: str) -> None:
    t = time.monotonic()
    with pytest.raises(ValueError, match=r"aliases"):
        parse_interfaces(cfg)
    ifaces, note = resolve(cfg, None, [port(0), port(1)], server_hostname="h", local_hostname="h",
                           root=FIX / "af_packet_veth", lspci=Stub())
    assert time.monotonic() - t < 0.5
    assert note == ALIAS_NOTE
    assert len(ifaces) == 2 and all(i.cfg_entry is None and i.kind is None for i in ifaces)


def test_anchors_without_aliases_are_fine() -> None:
    assert parse_interfaces("- interfaces: [&a veth0, veth1]\n") == (["veth0", "veth1"], None)


def test_non_string_entries_are_dropped_not_stringified() -> None:
    assert parse_interfaces("- interfaces: [veth0, [a, b], {k: v}]\n")[0] == ["veth0"]
    ifaces, _ = resolve("- interfaces: [[a, b], {k: v}]\n", None, [port(0), port(1)], server_hostname="h",
                        local_hostname="h", root=FIX / "af_packet_veth", lspci=Stub())
    assert ifaces[0].note and ifaces[0].note.startswith("interfaces[0] is a list, not a string")
    assert ifaces[1].note and ifaces[1].note.startswith("interfaces[1] is a dict, not a string")


@pytest.mark.parametrize("text, expected", [("2", 2), ("!!int 2", 2), ("'3'", 3), ("true", None),
                                            ("[1]", None),
                                            ("!!int -1", None)])
def test_port_limit_forms(text: str, expected: int | None) -> None:
    assert parse_interfaces(f"- interfaces: [a, b, c]\n  port_limit: {text}\n") == (["a", "b", "c"], expected)


def test_userspace_bound_device_does_not_scan_for_a_same_mac_interface(tmp_path: Path) -> None:
    dev = tmp_path / "sys/bus/pci/devices/0000:03:00.0"
    dev.mkdir(parents=True)
    (dev / "uevent").write_text("DRIVER=vfio-pci\n")
    other = tmp_path / "sys/class/net/eno1"
    (other / "device").mkdir(parents=True)                 # a physical interface elsewhere, same MAC
    (other / "address").write_text("aa:bb:cc:dd:ee:ff\n")
    ifaces, _ = resolve(None, None, [port(0, "aa:bb:cc:dd:ee:ff", "0000:03:00.0")], server_hostname="h",
                        local_hostname="h", root=tmp_path, lspci=Stub())
    assert ifaces[0].kernel_name is None and ifaces[0].driver == "vfio-pci"


FOUND = "found by MAC (no config entry)"
NO_ENTRY = "no config entry and no PCI address from the server"
MAC_A = "aa:bb:cc:dd:ee:01"


def net(root: Path, name: str, mac: str, *extra: str) -> None:
    """Creates root/sys/class/net/name with an address file (mac) and an empty subdirectory per extra."""
    d = root / "sys/class/net" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "address").write_text(mac + "\n")
    for e in extra:
        (d / e).mkdir()


def scan_one(root: Path, mac: str = MAC_A) -> PortIface:
    """The PortIface resolve() builds for one local port with this MAC, from the sysfs tree under root."""
    return resolve(None, None, [port(0, mac)], server_hostname="h", local_hostname="h", root=root,
                   lspci=Stub())[0][0]


def test_mac_scan_without_cfg_confirms_both_veths() -> None:
    ifaces, _ = run("af_packet_veth", None, [port(0, VETH_MACS[0]), port(1, VETH_MACS[1])])
    assert [(i.kernel_name, i.confirmed, i.note, i.cfg_entry) for i in ifaces] == [
        ("veth0", True, FOUND, None), ("veth1", True, FOUND, None)]


def test_mac_scan_after_a_count_mismatch() -> None:
    ifaces, note = run("af_packet_veth", "- interfaces: [veth0]\n",
                       [port(0, VETH_MACS[0]), port(1, VETH_MACS[1])])
    assert [(i.kernel_name, i.confirmed, i.note) for i in ifaces] == [
        ("veth0", True, FOUND), ("veth1", True, FOUND)]
    assert note and "1 interfaces but the server has 2" in note


def test_mac_scan_after_a_non_string_entry() -> None:
    ifaces, _ = run("af_packet_veth", "- interfaces: [[a], veth1]\n",
                    [port(0, VETH_MACS[0]), port(1, VETH_MACS[1])])
    assert ifaces[0].kernel_name == "veth0" and ifaces[0].note and FOUND in ifaces[0].note
    assert ifaces[1].cfg_entry == "veth1" and ifaces[1].kernel_name == "veth1" and ifaces[1].note is None


def test_mac_scan_ignores_a_vlan_on_the_same_mac(tmp_path: Path) -> None:
    net(tmp_path, "veth0", MAC_A)
    net(tmp_path, "veth0.10", MAC_A)
    (tmp_path / "sys/class/net/veth0.10/lower_veth0").mkdir()
    i = scan_one(tmp_path)
    assert (i.kernel_name, i.confirmed, i.note) == ("veth0", True, FOUND)


@pytest.mark.parametrize("extra", [("bonding",), ("bridge",), ("brport",), ("bonding_slave",)])
def test_mac_scan_never_counts_stacked_interfaces(tmp_path: Path, extra: tuple[str, ...]) -> None:
    net(tmp_path, "x0", MAC_A, *extra)
    i = scan_one(tmp_path)
    assert (i.kernel_name, i.confirmed, i.note) == (None, False, NO_ENTRY)


def test_mac_scan_bond_master_and_slave_give_no_name(tmp_path: Path) -> None:
    net(tmp_path, "bond0", MAC_A, "bonding")
    net(tmp_path, "eno1", MAC_A, "bonding_slave")
    assert scan_one(tmp_path).kernel_name is None


def test_mac_scan_never_counts_lo(tmp_path: Path) -> None:
    net(tmp_path, "lo", MAC_A)
    assert scan_one(tmp_path).kernel_name is None


def test_mac_scan_two_plain_interfaces_with_one_mac_give_no_name(tmp_path: Path) -> None:
    net(tmp_path, "a0", MAC_A)
    net(tmp_path, "a1", MAC_A)
    i = scan_one(tmp_path)
    assert (i.kernel_name, i.confirmed, i.note) == (None, False, NO_ENTRY)


def test_swapped_cfg_names_the_mac_carrier() -> None:
    ifaces, _ = run("af_packet_veth", "- interfaces: [veth1, veth0]\n",
                    [port(0, VETH_MACS[0]), port(1, VETH_MACS[1])])
    assert [(i.cfg_entry, i.kernel_name, i.confirmed, i.note) for i in ifaces] == [
        ("veth1", None, False, f"veth1 not confirmed: MAC {VETH_MACS[0]} is on veth0"),
        ("veth0", None, False, f"veth0 not confirmed: MAC {VETH_MACS[1]} is on veth1")]


def test_af_packet_mismatch_with_no_carrier_keeps_the_old_note(tmp_path: Path) -> None:
    net(tmp_path, "veth0", "aa:aa:aa:aa:aa:aa")
    ifaces, _ = resolve("- interfaces: [veth0]\n", None, [port(0, MAC_A)], server_hostname="h",
                        local_hostname="h", root=tmp_path, lspci=Stub())
    assert ifaces[0].note == f"veth0 not confirmed: no such interface with MAC {MAC_A} here"


def test_host_gate_blocks_the_mac_scan_without_a_cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("filesystem access behind the host gate")
    for target in ("listdir", "open", "stat"):
        monkeypatch.setattr(os, target, boom)
    monkeypatch.setattr(Path, "read_text", boom)
    monkeypatch.setattr(Path, "exists", boom)
    monkeypatch.setattr(Path, "is_dir", boom)
    monkeypatch.setattr("builtins.open", boom)
    stub = Stub()
    ifaces, note = resolve(None, None, [port(0, VETH_MACS[0])], server_hostname="a", local_hostname="b",
                           root=FIX / "af_packet_veth", lspci=stub)
    assert ifaces[0].kernel_name is None and note and "no local lookups" in note
    assert stub.calls == 0
