"""The server probe (`scripts/trex_probe.py`): its scrubber, its sysfs and iproute2 readers, and its sections
run against the fake TRex. The fake models no VF and no host, so the host section is tested on built sysfs
and /proc trees; the fake's bursts never end (a started profile sends until stopped, `trex-stats-model` in
tests/fake/unrecorded.py), so the traffic test covers the bounded wait and the stop, not a burst that
finishes on its own."""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from support import FIXTURES, ROOT

PROBE = ROOT / "scripts" / "trex_probe.py"


def _load() -> Any:
    """scripts/trex_probe.py as a module (it is a downloaded tool, not part of the package)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("trex_probe", PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load()
Scrubber = probe.Scrubber


def run_probe(f: Any, out: Path, *flags: str) -> dict[str, Any]:
    """Run the probe as its users do (`python trex_probe.py`) against fake `f`; its report."""
    r = subprocess.run(
        [
            sys.executable,
            str(PROBE),
            "--sync-port",
            str(f.rpc_port),
            "--async-port",
            str(f.pub_port),
            "--out",
            str(out),
            "--no-host",
            *flags,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    report: dict[str, Any] = json.loads(out.read_text())
    return report


def recorded_status(port: int) -> dict[str, Any]:
    """The recorded get_port_status result of `port` (tests/fixtures/rpc.jsonl)."""
    for line in (FIXTURES / "rpc.jsonl").read_text().splitlines():
        d = json.loads(line)
        if (
            isinstance(d, dict)
            and d["req"]["method"] == "get_port_status"
            and d["req"]["params"]["port_id"] == port
        ):
            result: dict[str, Any] = d["resp"]["result"]
            return result
    raise AssertionError("no recorded get_port_status")


# --- the scrubber


def test_scrubber_replaces_addresses_with_stable_tokens() -> None:
    s = Scrubber()
    text = (
        "52:52:18:89:91:EA to 52:52:18:89:91:ea via 10.1.2.3, 0000:3b:00.1 and 3b:00.1, fe80::1 at 12:30:00"
    )
    assert s.text(text) == "mac-1 to mac-1 via ip-1, pci-1 and pci-1, ip-2 at 12:30:00"


def test_scrubber_keeps_what_identifies_no_host() -> None:
    s = Scrubber()
    kept = ("00:00:00:00:00:00 ff:ff:ff:ff:ff:ff 02:00:00:00:00:01 0.0.0.0 127.0.0.1 v3.08 999.1.1.1 :: "
            "rx_q0_bytes")
    assert s.text(kept) == kept


def test_scrubber_replaces_registered_names_longest_first_and_finds_leaks() -> None:
    s = Scrubber()
    s.add("host", "lab")
    s.add("host", "lab.example.net")
    s.add("user", "me")  # under 3 characters: ignored, it would match inside words
    assert s.text("lab.example.net and lab, me") == "host-1 and host-2, me"
    assert s.leaks("seen on lab") == ["lab"] and s.leaks("host-2") == []


def test_scrubber_value_reaches_keys_and_nested_strings() -> None:
    s = Scrubber()
    s.add("if", "ens1f0v0")
    assert s.value({"ens1f0v0": ["mac 52:52:18:89:91:ea", 3, None]}) == {"if-1": ["mac mac-1", 3, None]}


# --- the host readers


def sysfs_tree(root: Path) -> Path:
    """A sysfs PCI tree with a PF (0000:3b:00.0, netdev pf0, 2 VFs), its VF 1 (0000:3b:00.3, netdev vf1) and a
    function with neither (0000:5e:00.0, bound to vfio-pci). Drivers are links into a drivers directory."""
    pci = root / "devices"
    drivers = root / "drivers"
    for d in ("mlx5_core", "vfio-pci"):
        (drivers / d).mkdir(parents=True)
    pf, vf, plain = pci / "0000:3b:00.0", pci / "0000:3b:00.3", pci / "0000:5e:00.0"
    for dev, drv, net in ((pf, "mlx5_core", "pf0"), (vf, "mlx5_core", "vf1"), (plain, "vfio-pci", None)):
        dev.mkdir(parents=True)
        (dev / "driver").symlink_to(drivers / drv)
        (dev / "vendor").write_text("0x15b3\n")
        (dev / "device").write_text("0x101e\n")
        if net:
            (dev / "net" / net).mkdir(parents=True)
    (pf / "sriov_numvfs").write_text("2\n")
    (pf / "virtfn0").symlink_to(pci / "0000:3b:00.2")
    (pf / "virtfn1").symlink_to(vf)
    (vf / "physfn").symlink_to(pf)
    return pci


def test_pci_function_tells_pf_vf_and_plain(tmp_path: Path) -> None:
    pci = sysfs_tree(tmp_path)
    assert probe.pci_function("0000:3b:00.0", pci) == {
        "pci": "0000:3b:00.0",
        "driver": "mlx5_core",
        "netdevs": ["pf0"],
        "vendor_device": "0x15b3:0x101e",
        "role": "pf",
        "sriov_numvfs": "2",
    }
    vf = probe.pci_function("0000:3b:00.3", pci)
    assert (vf["role"], vf["vf_index"], vf["pf"]) == (
        "vf",
        1,
        {"pci": "0000:3b:00.0", "netdevs": ["pf0"], "driver": "mlx5_core"},
    )
    assert probe.pci_function("0000:5e:00.0", pci)["role"] == "function"
    with pytest.raises(RuntimeError, match="no "):
        probe.pci_function("0000:99:00.0", pci)


# The shape of `ip -details -json link show` for a PF with VFs, from iproute2's ip/iplink.c print_vfinfo (keys
# vf, mac, vlan_list, rate, spoofchk, link_state, trust): no host with VFs has been recorded (the guest has
# none).
IP_JSON = json.dumps(
    [
        {
            "ifname": "pf0",
            "vfinfo_list": [
                {"vf": 0, "mac": "02:11:22:33:44:00", "spoofchk": True, "link_state": "auto", "trust": False},
                {
                    "vf": 1,
                    "mac": "02:11:22:33:44:01",
                    "vlan_list": [{"vlan": 120}],
                    "rate": {"max_tx": 0, "min_tx": 0},
                    "spoofchk": True,
                    "link_state": "auto",
                    "trust": False,
                },
            ],
        }
    ]
)


def test_vf_settings_picks_the_vf_by_index() -> None:
    assert probe.vf_settings(IP_JSON, 1)["vlan_list"] == [{"vlan": 120}]
    with pytest.raises(RuntimeError, match="no VF 5"):
        probe.vf_settings(IP_JSON, 5)


def test_probe_host_reads_each_port_and_a_vfs_settings_from_its_pf(tmp_path: Path) -> None:
    pci = sysfs_tree(tmp_path)
    ran: list[list[str]] = []

    def run(argv: list[str]) -> str:
        """Record the command; answer `ip` with IP_JSON and `ethtool -i` with a driver line."""
        ran.append(argv)
        return IP_JSON if argv[0] == "ip" else "driver: mlx5_core\n"

    info = {
        "ports": [
            {"index": 0, "pci_addr": "0000:3b:00.0"},
            {"index": 1, "pci_addr": "0000:3b:00.3"},
            {"index": 2, "pci_addr": "N/A"},
            {"index": 3, "pci_addr": "0000:5e:00.0"},
        ]
    }
    host = probe.probe_host(info, pci, run)["ports"]
    assert host["1"]["vf_settings"]["result"]["spoofchk"] is True
    assert ["ip", "-details", "-json", "link", "show", "dev", "pf0"] in ran
    assert "vf_settings" not in host["0"] and host["0"]["ethtool"]["ok"]
    assert host["2"]["function"] == {"ok": False, "error": "the server reports no PCI address"}
    assert "ethtool" not in host["3"]  # bound to vfio: no netdev to ask


def test_a_vf_whose_pf_has_no_netdev_says_so(tmp_path: Path) -> None:
    pci = sysfs_tree(tmp_path)
    (pci / "0000:3b:00.0" / "net" / "pf0").rmdir()
    host = probe.probe_host({"ports": [{"index": 1, "pci_addr": "0000:3b:00.3"}]}, pci, lambda argv: "")
    assert "no netdev" in host["ports"]["1"]["vf_settings"]["error"]


def test_trex_process_reads_the_command_line_and_its_cfg(tmp_path: Path) -> None:
    cfg = tmp_path / "trex.yaml"
    cfg.write_text("- port_limit: 4\n")
    (tmp_path / "17").mkdir()
    (tmp_path / "17" / "cmdline").write_bytes(b"./_t-rex-64\0-i\0--cfg\0" + str(cfg).encode() + b"\0")
    (tmp_path / "self").mkdir()
    got = probe.trex_process(tmp_path)
    assert (
        got["argv"] == ["./_t-rex-64", "-i", "--cfg", str(cfg)]
        and got["cfg"]["result"] == "- port_limit: 4\n"
    )
    (tmp_path / "17" / "cmdline").write_bytes(b"sleep\0")
    with pytest.raises(RuntimeError, match="no TRex server process"):
        probe.trex_process(tmp_path)


# --- write guard


class StatusClient:
    """A client stub for `writable`: `_transmit` answers get_port_status with `status`; it owns `mine`."""

    def __init__(self, status: dict[str, Any], mine: list[int]) -> None:
        """Keep the status to answer and the ports this session owns."""
        self.status, self.mine = status, mine

    def _transmit(self, method: str, params: dict[str, Any]) -> Any:
        """A successful RC-like reply carrying the status."""
        status = self.status

        class RC:
            """The truthy reply `rpc` reads with data()."""

            def __bool__(self) -> bool:
                """Success."""
                return True

            def data(self) -> dict[str, Any]:
                """The status."""
                return status

        return RC()

    def get_acquired_ports(self) -> list[int]:
        """The ports this session owns."""
        return self.mine


def test_writable_refuses_a_port_owned_elsewhere_or_busy() -> None:
    idle = recorded_status(1)  # recorded IDLE, no owner
    assert probe.writable(StatusClient(idle, []), 1) is None
    owned = {**idle, "owner": "someone"}
    assert "owned by someone" in (probe.writable(StatusClient(owned, []), 1) or "")
    assert probe.writable(StatusClient(owned, [1]), 1) is None  # this session's own
    assert probe.writable(StatusClient({**idle, "state": "TX"}, []), 1) == "the port is TX"


# --- against the fake


def test_read_only_probe_reports_each_ports_own_xstats_and_scrubs(tmp_path: Path, fake_trex: Any) -> None:
    f = fake_trex("--xstats-short", "1")
    out = tmp_path / "probe.json"
    report = run_probe(f, out)
    assert {p: v["xstats_counts"] for p, v in report["ports"].items()} == {
        "0": {"names": 13, "values": 13},
        "1": {"names": 9, "values": 9},
    }
    assert report["server"]["system_info"]["result"]["ports"][0]["is_virtual"] is True
    text = out.read_text()
    for private in ("52:52:18:89:91:ea", "8e:4e:97:11:bf:a8", "1.1.1.1", "2.2.2.2", "trex-vm"):
        assert private not in text  # the recorded guest's MACs, IPs and name
    assert {v["status"]["result"]["owner"] for v in report["ports"].values()} == {""}


def test_write_probe_puts_each_attribute_back_and_releases(tmp_path: Path, fake_trex: Any) -> None:
    f = fake_trex()
    report = run_probe(f, tmp_path / "probe.json", "--write", "0")
    w = report["writes"]["0"]
    assert w["attributes_after"] == w["attributes_before"]
    assert all(
        w["attributes"][k]["ok"] for k in ("promiscuous", "promiscuous_restore", "multicast", "led_on")
    )
    assert w["service_on"]["ok"] and w["service_restore"]["ok"] and w["capture"]["ok"] and w["release"]["ok"]
    after = run_probe(f, tmp_path / "after.json")
    assert after["ports"]["0"]["status"]["result"]["owner"] == ""
    assert after["ports"]["0"]["status"]["result"]["service"] is False


def test_traffic_probe_stops_an_unfinished_burst_and_leaves_both_ports_idle(
    tmp_path: Path, fake_trex: Any
) -> None:
    f = fake_trex()
    report = run_probe(f, tmp_path / "probe.json", "--traffic", "0:1", "--burst-timeout", "0.5")
    t = report["traffic"]
    for name in ("own_mac", "foreign_mac", "latency"):
        assert t[name]["ok"] and t[name]["result"]["finished"] is False, t[name]
        assert t[name]["result"]["rx_pkts"] == t[name]["result"]["tx_pkts"] > 0
    assert t["release"]["ok"]
    after = run_probe(f, tmp_path / "after.json")
    assert after["ports"]["0"]["status"]["result"]["state"] != "TX"
    assert after["ports"]["0"]["status"]["result"]["owner"] == ""


def test_write_on_a_transmitting_port_is_skipped(tmp_path: Path, fake_trex: Any) -> None:
    """A port another session left transmitting is not touched: the probe's own skip, not the server's."""
    f = fake_trex()
    code = (
        f"import sys; from trex_web_gui.trexapi.loader import load; load()\n"
        f"from trex.stl.api import STLClient, STLStream, STLPktBuilder, STLTXCont\n"
        f"from scapy.layers.l2 import Ether\n"
        f"c = STLClient(server='127.0.0.1', sync_port={f.rpc_port}, async_port={f.pub_port}, "
        f"verbose_level='none', username='other')\n"
        f"c.connect(); c.acquire(ports=[0])\n"
        f"c.add_streams(STLStream(packet=STLPktBuilder(pkt=Ether()/(b'x'*60)), mode=STLTXCont(pps=10)), "
        f"ports=[0])\n"
        f"c.start(ports=[0], mult='10pps'); c.disconnect(stop_traffic=False, release_ports=False)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    report = run_probe(f, tmp_path / "probe.json", "--write", "0")
    skipped = report["writes"]["0"]["skipped"]  # the owner's name is scrubbed to a user token
    assert re.fullmatch(
        r"owned by user-\d+ in another session \(a trex-web-gui holding it: release it there "
        r"first\)",
        skipped,
    ), skipped


def test_write_probe_leaves_service_mode_on_when_it_was_on(tmp_path: Path, fake_trex: Any) -> None:
    """A port another session left in service mode (and released) is in service mode after the probe too."""
    f = fake_trex()
    code = (
        "from trex_web_gui.trexapi.loader import load; load()\n"
        "from trex.stl.api import STLClient\n"
        f"c = STLClient(server='127.0.0.1', sync_port={f.rpc_port}, async_port={f.pub_port}, "
        "verbose_level='none', username='other')\n"
        "c.connect(); c.acquire(ports=[0]); c.set_service_mode(ports=[0], enabled=True)\n"
        "c.release(ports=[0]); c.disconnect()\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    w = run_probe(f, tmp_path / "probe.json", "--write", "0")["writes"]["0"]
    assert w["attributes_before"]["service"] is True and w["attributes_after"] == w["attributes_before"]
    after = run_probe(f, tmp_path / "after.json")
    assert after["ports"]["0"]["status"]["result"]["service"] is True
