"""Probe a TRex server for what each of its ports lists and supports, for a bug report.

Writes one JSON file with host-identifying text replaced by stable tokens (`mac-1`, `ip-2`, `pci-1`, `if-3`,
`host-1`, `user-1`, `vlan-1`, `path-1`), so it can be shared from a server whose names and addresses are
private. Built for servers whose ports mix physical and virtual functions (a Mellanox VF lists fewer counters
and supports fewer attributes than its physical port), but it runs against any TRex server in STL mode.

Run it on the TRex host, with the Python trex-web-gui is installed under (it uses that install's vendored TRex
client, so it works with any trex-web-gui from 1.0 on):

    "$(uv tool dir)/trex-web-gui/bin/python" trex_probe.py --out trex-probe.json

A tool, not part of the app: it lives outside the package (so its raw RPC getters are not the app's, which
all go through trexapi/rpc_escape_hatch.py) and is downloaded on its own, as the README's "Probe a server for
a bug report" section says.

Sections:

- `server`, `ports` (always; read-only, acquires nothing): the version, the system info, and for each port its
  status, its formatted attributes and support flags, and its own xstats names and values.
- `host` (when run on the TRex host; `--no-host` skips it): for each port, its PCI function (physical or
  virtual), driver and netdev; for a virtual function, the physical port's settings for it (spoofchk, trust,
  link state, rate, VLAN; `ip -details -json link show`); `ethtool -i`; the TRex server's command line and its
  config file.
- `writes` (only with `--write PORTS`): on each listed port that is idle and not owned by anyone else, it
  acquires the port, toggles promiscuous, multicast, LED and flow control (and the link, only with
  `--try-link`), turns service mode on and off and runs a short capture, records what the server refuses, puts
  each attribute back and releases the port.
- `traffic` (only with `--traffic TX:RX`): three bursts of 1000 packets at 1000 pps from TX, read back on RX
  by their flow-stats or latency counters: one with the port's own source MAC, one with another source MAC (a
  VF with spoofchk on drops it), one with latency stats.

Nothing in the read-only sections changes the server. The opt-in ones change only ports that nobody else owns,
and only while they are idle."""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

PROBE_VERSION = 1
FOREIGN_MAC = "02:00:00:00:00:01"  # a locally administered source MAC no port owns (the spoofchk test)
KEEP = {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", FOREIGN_MAC, "0.0.0.0", "127.0.0.1", "255.255.255.255"}
BUSY = ("TX", "PAUSE", "PCAP_TX", "ASTF_PARSE", "ASTF_BUILD", "ASTF_CLEANUP")
SYSFS_PCI = Path("/sys/bus/pci/devices")
BURST_PKTS = 1000
BURST_PPS = 1000


class Scrubber:
    """Replaces host-identifying text with stable tokens: the same original always gets the same token, so the
    report still shows which ports share a MAC, a PCI device or a VLAN. Names registered with `add` (hosts,
    user names, interface names, home directories) are replaced wherever they appear as a whole word; MAC,
    PCI, IPv4 and IPv6 addresses are found by pattern. `leaks` lists the registered names still present."""

    MAC = re.compile(r"(?i)(?<![0-9a-f:])[0-9a-f]{2}(?::[0-9a-f]{2}){5}(?![0-9a-f:])")
    PCI = re.compile(r"(?i)(?<![0-9a-f:])(?:[0-9a-f]{4}:)?[0-9a-f]{2}:[0-9a-f]{2}\.[0-7](?![0-9a-f])")
    IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
    IPV6 = re.compile(r"(?i)(?<![0-9a-f:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![0-9a-f:])")

    def __init__(self) -> None:
        """No names registered; no tokens handed out."""
        self.tokens: dict[tuple[str, str], str] = {}
        self.counts: dict[str, int] = {}
        self.names: dict[str, str] = {}  # original -> kind

    def token(self, kind: str, original: str) -> str:
        """The token for `original` of `kind`, the next free `<kind>-N` the first time it is seen."""
        key = (kind, original.lower())
        if key not in self.tokens:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            self.tokens[key] = f"{kind}-{self.counts[kind]}"
        return self.tokens[key]

    def add(self, kind: str, name: str | None) -> None:
        """Register `name` (a host, user, interface or path) for replacement; names under 3 characters, which
        would match inside ordinary words, are ignored."""
        if name and len(name) >= 3 and name not in KEEP:
            self.names[name] = kind

    def text(self, s: str) -> str:
        """`s` with every registered name (longest first) and every address replaced by its token."""
        for name in sorted(self.names, key=len, reverse=True):
            pattern = _whole(name)
            if pattern.search(s):  # a token only for a name that appears (tokens hold no backslash)
                s = pattern.sub(self.token(self.names[name], name), s)
        s = self.MAC.sub(lambda m: m[0] if m[0].lower() in KEEP else self.token("mac", m[0]), s)
        s = self.PCI.sub(lambda m: self.token("pci", m[0] if FULL_PCI.match(m[0]) else f"0000:{m[0]}"), s)
        s = self.IPV4.sub(lambda m: m[0] if m[0] in KEEP or not _is_ipv4(m[0]) else self.token("ip", m[0]), s)
        return self.IPV6.sub(lambda m: self.token("ip", m[0]) if _is_ipv6(m[0]) else m[0], s)

    def value(self, v: Any) -> Any:
        """`v` (JSON data) with `text` applied to every string, dictionary keys included."""
        if isinstance(v, str):
            return self.text(v)
        if isinstance(v, dict):
            return {self.text(str(k)): self.value(x) for k, x in v.items()}
        if isinstance(v, list | tuple):
            return [self.value(x) for x in v]
        return v

    def leaks(self, s: str) -> list[str]:
        """The registered names still present in `s` as whole words (empty for a scrubbed text)."""
        return [n for n in self.names if _whole(n).search(s)]


FULL_PCI = re.compile(r"(?i)[0-9a-f]{4}:")  # a PCI address with its domain (0000:3b:00.0, not 3b:00.0)


def _whole(name: str) -> re.Pattern[str]:
    """`name` as a whole word: not inside a longer word ("other" leaves "another" alone)."""
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")


def _is_ipv4(s: str) -> bool:
    """True when `s` is a dotted IPv4 address (each part 0-255)."""
    try:
        ipaddress.IPv4Address(s)
    except ValueError:
        return False
    return True


def _is_ipv6(s: str) -> bool:
    """True when `s` is an IPv6 address other than `::` (a time such as 12:30:00 is not one)."""
    try:
        return ipaddress.IPv6Address(s) != ipaddress.IPv6Address("::")
    except ValueError:
        return False


def step(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """`{"ok": true, "result": fn(*args, **kwargs)}`, or `{"ok": false, "error": "<Type>: <message>"}` when it
    raises: one refused step never stops the probe."""
    try:
        return {"ok": True, "result": fn(*args, **kwargs)}
    except Exception as e:  # noqa: BLE001 - every refusal is data for the report
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def rpc(c: Any, method: str, params: dict[str, Any] | None = None) -> Any:
    """The data of one raw RPC on client `c`; RuntimeError with the server's text when it is refused."""
    rc = c._transmit(method, params or {})
    if not rc:
        raise RuntimeError(rc.err())
    return rc.data()


def probe_server(c: Any) -> dict[str, Any]:
    """The `server` section: version, system info and supported commands."""
    return {
        "version": step(lambda: rpc(c, "get_version")),
        "system_info": step(lambda: rpc(c, "get_system_info")),
        "supported_cmds": step(lambda: rpc(c, "get_supported_cmds")),
    }


def probe_port(c: Any, pid: int) -> dict[str, Any]:
    """One port's read-only facts: raw status, the client's formatted attributes, and the port's own xstats
    names and values with their counts (a mismatch is what vendor patch 17 fixed)."""
    names = step(lambda: rpc(c, "get_port_xstats_names", {"port_id": pid})["xstats_names"])
    values = step(lambda: rpc(c, "get_port_xstats_values", {"port_id": pid})["xstats_values"])
    out = {
        "status": step(lambda: rpc(c, "get_port_status", {"port_id": pid})),
        "formatted": step(lambda: c.get_port_attr(pid)),
        "xstats_names": names,
        "xstats_values": values,
    }
    if names["ok"] and values["ok"]:
        out["xstats_counts"] = {"names": len(names["result"]), "values": len(values["result"])}
    return out


def run_cmd(argv: list[str]) -> str:
    """The stdout of `argv` (10 s at most); RuntimeError with its stderr when it fails or is not installed."""
    if shutil.which(argv[0]) is None:
        raise RuntimeError(f"{argv[0]} is not installed")
    r = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"{argv[0]} exited {r.returncode}")
    return r.stdout


def pci_function(pci: str, sysfs: Path = SYSFS_PCI) -> dict[str, Any]:
    """What sysfs says of PCI function `pci`: `role` ("vf" with a `physfn` link, "pf" with `sriov_numvfs`,
    else "function"), its driver, its netdevs, and for a VF the physical function's address, netdevs and this
    VF's index under it (the `virtfnN` link that points back at it)."""
    dev = sysfs / pci
    if not dev.exists():
        raise RuntimeError(f"no {dev}")
    out: dict[str, Any] = {
        "pci": pci,
        "driver": _link_name(dev / "driver"),
        "netdevs": _netdevs(dev),
        "vendor_device": f"{_read(dev / 'vendor')}:{_read(dev / 'device')}",
    }
    if (dev / "physfn").exists():
        pf = (dev / "physfn").resolve()
        out["role"] = "vf"
        out["pf"] = {"pci": pf.name, "netdevs": _netdevs(pf), "driver": _link_name(pf / "driver")}
        for link in pf.glob("virtfn*"):
            if link.resolve() == dev.resolve():
                out["vf_index"] = int(link.name[len("virtfn") :])
    elif (dev / "sriov_numvfs").exists():
        out["role"] = "pf"
        out["sriov_numvfs"] = _read(dev / "sriov_numvfs")
    else:
        out["role"] = "function"
    return out


def _read(p: Path) -> str | None:
    """The stripped text of `p`, or None when it cannot be read."""
    try:
        return p.read_text().strip()
    except OSError:
        return None


def _link_name(p: Path) -> str | None:
    """The last part of the symlink `p` points at (a driver's name), or None when there is none."""
    return p.resolve().name if p.is_symlink() else None


def _netdevs(dev: Path) -> list[str]:
    """The kernel netdev names of PCI function `dev` (none for a port bound to vfio or uio)."""
    net = dev / "net"
    return sorted(p.name for p in net.iterdir()) if net.is_dir() else []


def vf_settings(ip_json: str, index: int) -> dict[str, Any]:
    """The physical port's settings for VF `index`, from `ip -details -json link show dev <pf>`: its
    `vfinfo_list` entry (spoofchk, trust, link_state, rates, VLAN, MAC); RuntimeError when it lists none."""
    links = json.loads(ip_json)
    for link in links:
        for vf in link.get("vfinfo_list", []):
            if vf.get("vf") == index:
                return dict(vf)
    raise RuntimeError(f"the physical port lists no VF {index}")


def trex_process(proc: Path = Path("/proc")) -> dict[str, Any]:
    """The running TRex server's command line (`_t-rex-64` or `t-rex-64`) and the config file it reads
    (`--cfg`, else /etc/trex_cfg.yaml)."""
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            argv = (d / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        args = [a.decode(errors="replace") for a in argv if a]
        if args and Path(args[0]).name in ("_t-rex-64", "t-rex-64", "_t-rex-64-debug"):
            cfg = args[args.index("--cfg") + 1] if "--cfg" in args[:-1] else "/etc/trex_cfg.yaml"
            return {"argv": args, "cfg_path": cfg, "cfg": step(Path(cfg).read_text)}
    raise RuntimeError("no TRex server process found on this host")


def probe_host(
    system_info: dict[str, Any], sysfs: Path = SYSFS_PCI, run: Callable[[list[str]], str] = run_cmd
) -> dict[str, Any]:
    """The `host` section for the ports `system_info` lists: each port's PCI function, its `ethtool -i`, and
    for a VF its physical port's settings for it; then the TRex process."""
    ports = {}
    for p in system_info.get("ports", []):
        pci = p.get("pci_addr")
        entry: dict[str, Any] = {"pci_addr": pci}
        if not pci or pci == "N/A":
            entry["function"] = {"ok": False, "error": "the server reports no PCI address"}
        else:
            fn = step(pci_function, pci, sysfs)
            entry["function"] = fn
            f = fn.get("result") or {}
            if f.get("netdevs"):
                entry["ethtool"] = step(run, ["ethtool", "-i", f["netdevs"][0]])
            if f.get("role") == "vf":
                pf_dev = (f["pf"]["netdevs"] or [None])[0]
                entry["vf_settings"] = step(_vf_settings_of, pf_dev, f.get("vf_index"), run)
        ports[str(p.get("index"))] = entry
    return {"ports": ports, "trex_process": step(trex_process)}


def _vf_settings_of(pf_dev: str | None, index: int | None, run: Callable[[list[str]], str]) -> dict[str, Any]:
    """The settings of VF `index` read from its physical port `pf_dev`; RuntimeError (`_no_pf`) when either
    is unknown."""
    if pf_dev is None or index is None:
        _no_pf(pf_dev)
    assert pf_dev is not None and index is not None
    return vf_settings(run(["ip", "-details", "-json", "link", "show", "dev", pf_dev]), index)


def _no_pf(netdev: str | None) -> None:
    """Raises why a VF's settings cannot be read: its physical port has no netdev, or its index is unknown."""
    raise RuntimeError(
        "the physical port has no netdev (bound to vfio or uio?)"
        if netdev is None
        else "the VF's index under its physical port is unknown"
    )


def writable(c: Any, pid: int) -> str | None:
    """Why port `pid` must not be changed (owned by another session, or busy), or None when it may be."""
    st = rpc(c, "get_port_status", {"port_id": pid})
    if st.get("owner") and pid not in c.get_acquired_ports():
        return (
            f"owned by {st['owner']} in another session (a trex-web-gui holding it: release it there first)"
        )
    if st.get("state") in BUSY:
        return f"the port is {st['state']}"
    return None


def probe_writes(c: Any, pid: int, try_link: bool) -> dict[str, Any]:
    """The `writes` entry of port `pid`: refused when `writable` says so; otherwise acquire, each attribute
    toggled and put back, service mode on and off, a 2 s capture of at most 10 packets, release."""
    why = writable(c, pid)
    if why is not None:
        return {"skipped": why}
    out: dict[str, Any] = {"acquire": step(lambda: c.acquire(ports=[pid]))}
    if not out["acquire"]["ok"]:
        return out
    try:
        st = rpc(c, "get_port_status", {"port_id": pid})["attr"]
        before = {
            "promiscuous": st["promiscuous"]["enabled"],
            "multicast": st["multicast"]["enabled"],
            "link_up": st["link"]["up"],
            "flow_ctrl": st.get("fc", {}).get("mode"),
        }
        out["attributes_before"] = before
        attrs: dict[str, Any] = {}
        for name in ("promiscuous", "multicast"):
            attrs[name] = step(c.set_port_attr, ports=[pid], **{name: not before[name]})
            attrs[f"{name}_restore"] = step(c.set_port_attr, ports=[pid], **{name: before[name]})
        attrs["led_on"] = step(lambda: c.set_port_attr(ports=[pid], led_on=True))
        attrs["led_restore"] = step(lambda: c.set_port_attr(ports=[pid], led_on=False))
        if before["flow_ctrl"] is not None and before["flow_ctrl"] >= 0:
            attrs["flow_ctrl"] = step(
                lambda: c.set_port_attr(ports=[pid], flow_ctrl=(before["flow_ctrl"] + 1) % 4)
            )
            attrs["flow_ctrl_restore"] = step(
                lambda: c.set_port_attr(ports=[pid], flow_ctrl=before["flow_ctrl"])
            )
        else:
            attrs["flow_ctrl"] = {"ok": False, "error": "the port reports no flow-control mode"}
        if try_link:
            attrs["link_down"] = step(lambda: c.set_port_attr(ports=[pid], link_up=False))
            attrs["link_restore"] = step(lambda: c.set_port_attr(ports=[pid], link_up=before["link_up"]))
        out["attributes"] = attrs
        out["service_on"] = step(lambda: c.set_service_mode(ports=[pid], enabled=True))
        out["capture"] = step(lambda: _capture(c, pid))
        out["service_off"] = step(lambda: c.set_service_mode(ports=[pid], enabled=False))
        after = rpc(c, "get_port_status", {"port_id": pid})["attr"]
        out["attributes_after"] = {
            "promiscuous": after["promiscuous"]["enabled"],
            "multicast": after["multicast"]["enabled"],
            "link_up": after["link"]["up"],
            "flow_ctrl": after.get("fc", {}).get("mode"),
        }
    finally:
        out["release"] = step(lambda: c.release(ports=[pid]))
    return out


def _capture(c: Any, pid: int) -> dict[str, Any]:
    """A capture of at most 10 packets on `pid`'s RX for 2 s: how many it caught (0 on a quiet link)."""
    cap = c.start_capture(rx_ports=[pid], limit=10)
    time.sleep(2)
    got: list[Any] = []
    c.stop_capture(cap["id"], output=got)
    return {"packets": len(got)}


def probe_traffic(c: Any, tx: int, rx: int, wait_s: float) -> dict[str, Any]:
    """Three bursts from `tx`, each read back on `rx` by its pg_id: flow stats with the port's own source MAC,
    flow stats with FOREIGN_MAC, latency with its own. Both ports must pass `writable`; each is acquired for
    the run and released after. A burst still sending after `wait_s` is stopped and says `finished: false`;
    every burst stops `tx` before the next step, so no traffic outlives the probe."""
    for pid in {tx, rx}:
        why = writable(c, pid)
        if why is not None:
            return {"skipped": f"port {pid}: {why}"}
    from scapy.layers.inet import IP, UDP
    from scapy.layers.l2 import Ether
    from trex.common.trex_exceptions import TRexTimeoutError
    from trex.stl.api import STLFlowLatencyStats, STLFlowStats, STLPktBuilder, STLStream, STLTXSingleBurst

    def burst(pg: int, src: str | None, latency: bool) -> dict[str, Any]:
        """One burst of BURST_PKTS on pg_id `pg`; the tx and rx packet counts of that pg_id."""
        ether = Ether(src=src) if src else Ether()
        pkt = STLPktBuilder(
            pkt=ether / IP(src="16.0.0.1", dst="48.0.0.1") / UDP(sport=1025, dport=12) / (b"x" * 18)
        )
        stats = STLFlowLatencyStats(pg_id=pg) if latency else STLFlowStats(pg_id=pg)
        c.remove_all_streams(ports=[tx])
        c.add_streams(
            STLStream(
                packet=pkt, mode=STLTXSingleBurst(total_pkts=BURST_PKTS, pps=BURST_PPS), flow_stats=stats
            ),
            ports=[tx],
        )
        # not the xstats: before vendor patch 17 that read fails where ports list different counters
        c.clear_stats(ports=[tx, rx], clear_xstats=False)
        finished = True
        try:
            c.start(ports=[tx])
            c.wait_on_traffic(ports=[tx], timeout=wait_s)
        except TRexTimeoutError:
            finished = False
        finally:
            c.stop(ports=[tx])
        time.sleep(1)  # the last packets' RX counters
        s = c.get_pgid_stats([pg])
        key = "latency" if latency else "flow_stats"
        f = s.get("flow_stats", {}).get(pg, {})
        out = {
            "finished": finished,
            "tx_pkts": f.get("tx_pkts", {}).get(tx),
            "rx_pkts": f.get("rx_pkts", {}).get(rx),
        }
        if latency:
            lat = s.get(key, {}).get(pg, {}).get("latency", {})
            out["latency_us"] = {k: lat.get(k) for k in ("average", "total_min", "total_max")}
            out["err_cntrs"] = s.get(key, {}).get(pg, {}).get("err_cntrs")
        return out

    out: dict[str, Any] = {"tx": tx, "rx": rx, "acquire": step(lambda: c.acquire(ports=sorted({tx, rx})))}
    if not out["acquire"]["ok"]:
        return out
    try:
        out["own_mac"] = step(lambda: burst(11, None, False))
        out["foreign_mac"] = step(lambda: burst(12, FOREIGN_MAC, False))
        out["latency"] = step(lambda: burst(13, None, True))
    finally:
        out["cleanup"] = step(lambda: c.remove_all_streams(ports=[tx]))
        out["release"] = step(lambda: c.release(ports=sorted({tx, rx})))
    return out


def build_parser() -> argparse.ArgumentParser:
    """The probe's command line."""
    p = argparse.ArgumentParser(prog="trex_probe.py", description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--server", default="127.0.0.1", help="the TRex server (default 127.0.0.1)")
    p.add_argument("--sync-port", type=int, default=4501)
    p.add_argument("--async-port", type=int, default=4500)
    p.add_argument(
        "--out", type=Path, default=Path("trex-probe.json"), help="the report (default trex-probe.json)"
    )
    p.add_argument("--no-host", action="store_true", help="skip the host section (not run on the TRex host)")
    p.add_argument(
        "--write",
        default="",
        metavar="PORTS",
        help="comma-separated idle ports nobody else owns to try attributes, service mode and capture on",
    )
    p.add_argument(
        "--try-link",
        action="store_true",
        help="with --write, also take each port's link down and up (a physical port's VFs go down too)",
    )
    p.add_argument(
        "--traffic", default="", metavar="TX:RX", help="send three short bursts from TX, counted on RX"
    )
    p.add_argument(
        "--burst-timeout",
        type=float,
        default=10.0,
        metavar="S",
        help="stop a burst still sending after this many seconds (default 10)",
    )
    p.add_argument("--no-scrub", action="store_true", help="keep names and addresses (for your own use only)")
    return p


def _ports(text: str) -> list[int]:
    """`0,2` as [0, 2]; an empty text as []."""
    return [int(x) for x in text.split(",") if x.strip()]


def main(argv: list[str] | None = None) -> int:
    """Connect, run the sections asked for, scrub, write the report, print a summary; 0 on success, 1 when the
    server cannot be reached or a registered name survived scrubbing (nothing is written then)."""
    a = build_parser().parse_args(argv)
    from trex_web_gui.trexapi.loader import load

    load()
    from trex.stl.api import STLClient

    c = STLClient(server=a.server, sync_port=a.sync_port, async_port=a.async_port, verbose_level="none")
    try:
        c.connect()
    except Exception as e:  # noqa: BLE001
        print(f"cannot connect to {a.server}:{a.sync_port}: {e}", file=sys.stderr)
        return 1
    report: dict[str, Any] = {"probe_version": PROBE_VERSION, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        report["server"] = probe_server(c)
        info = report["server"]["system_info"].get("result") or {}
        report["ports"] = {str(pid): probe_port(c, pid) for pid in range(info.get("port_count", 0))}
        if not a.no_host:
            report["host"] = step(lambda: probe_host(info))
        if a.write:
            report["writes"] = {str(pid): probe_writes(c, pid, a.try_link) for pid in _ports(a.write)}
        if a.traffic:
            tx, rx = (int(x) for x in a.traffic.split(":"))
            report["traffic"] = probe_traffic(c, tx, rx, a.burst_timeout)
    finally:
        c.disconnect()
    s = Scrubber()
    for kind, name in (
        ("host", socket.gethostname()),
        ("host", socket.getfqdn()),
        ("host", info.get("hostname")),
        ("user", getpass.getuser()),
        ("path", str(Path.home())),
    ):
        s.add(kind, name)
    for st in (report["ports"][p]["status"].get("result") or {} for p in report["ports"]):
        s.add("user", st.get("owner"))
    host = (report.get("host") or {}).get("result") or {}
    for entry in host.get("ports", {}).values():
        f = entry.get("function", {}).get("result") or {}
        for d in [*f.get("netdevs", []), *(f.get("pf") or {}).get("netdevs", [])]:
            s.add("if", d)
        vf = (entry.get("vf_settings") or {}).get("result")
        if isinstance(vf, dict):
            for key in ("vlan", "qos"):
                if isinstance(vf.get(key), int) and vf[key]:
                    vf[key] = s.token("vlan" if key == "vlan" else "qos", str(vf[key]))
            for v in vf.get("vlan_list", []) or []:
                if isinstance(v, dict) and isinstance(v.get("vlan"), int) and v["vlan"]:
                    v["vlan"] = s.token("vlan", str(v["vlan"]))
    out = report if a.no_scrub else s.value(report)
    text = json.dumps(out, indent=2, sort_keys=True, default=str)
    if not a.no_scrub and s.leaks(text):
        print(f"not written: {len(s.leaks(text))} name(s) survived scrubbing", file=sys.stderr)
        return 1
    a.out.write_text(text + "\n")
    print(
        f"wrote {a.out} ({len(text)} bytes)" + ("" if a.no_scrub else f"; replaced {s.counts or 'nothing'}")
    )
    for pid, p in out["ports"].items():
        n = p.get("xstats_counts")
        print(
            f"port {pid}: xstats {n['names']} names, {n['values']} values"
            if n
            else f"port {pid}: xstats unread"
        )
    print("Read the file before sharing it: the scrubber replaces what it recognises, nothing more.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
