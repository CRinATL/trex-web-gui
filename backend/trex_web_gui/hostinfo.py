"""Port interface names, from the TRex config file and the local sysfs: a pure, read-only resolver.

What it answers: for each TRex port, which kernel interface or PCI device is behind it. Everything it says is
either read from the cfg, or confirmed against the port (MAC or PCI address) and only after the host-identity
gate (the server's hostname equals this backend's). It never runs anything but one `lspci -mm -D`, never
writes, and never makes a TRex RPC.

The cfg format, as settled at the pin (trex-core 27e0153b5), each citation re-read for Task 1:
  - The file is a YAML sequence and TRex takes its first element, then stops:
    src/platform_cfg.cpp:598-601 "for(unsigned i=0;i<doc.size();i++) { doc[i] >> *this; break;"
    A top-level map makes `doc[i]` throw, and TRex prints the error and exits:
    src/platform_cfg.cpp:603-606 "} catch ( const std::exception& e ) { std::cout << e.what()".
  - Each entry is read as a string:
    src/platform_cfg.cpp:429-434 "node[i] >> fi; plat_info.m_if_list.push_back(fi);"
  - Port index = position in `interfaces`, keyed by the port's `id`, never by arrival order: get_system_info
    returns the ports unsorted (tests/fixtures/p3b/rpc.jsonl:7 "\\"index\\": 1" is listed
    before index 0).
  - A `dummy` entry takes an index:
    src/main_dpdk.cpp:6471-6476 "g_opts->m_dummy_count++;"
    and dummies count in the port total:
    src/main_dpdk.cpp:4240 "m_max_ports = rte_eth_dev_count_avail() + CGlobalInfo::m_options.m_dummy_count;"
  - Classification order (check_pdev_vdev_dummy): dummy, then a string containing `--vdev`, then a string with
    no `:` is an af_packet interface name, anything else is PCI:
    src/main_dpdk.cpp:6494 "// not PCI, assume af-packet".
  - PCI port order follows the cfg: src/main_dpdk.cpp:7275 "COLD_FUNC void reorder_dpdk_ports()" and
    src/utils/utl_port_map.cpp:196 "int CPciPorts::get_map_args(". The i-th non-dummy vdev is port i:
    src/main_dpdk.cpp:7323-7334 "if ( CTVPort(i).is_dummy() ) { continue; } lp->set_map(i, if_index);".
  - `port_limit` clamps the list:
    src/main_dpdk.cpp:6360-6367 "if (cg->m_port_limit > cg->m_if_list.size() ) {".
  - `description` is server-side fact: src/dpdk_trex_port_attr.cpp:51-63 "intf_info_st.description = envvar;"
    and for a dummy port src/trex_port_attr.h:240-242 "Dummy port". It is not verified here.

PyYAML's SafeLoader reads unquoted `03:00.0` and `0000:03:00.0` as YAML 1.1 sexagesimal numbers (180.0), so
the loader below has no implicit int or float resolver and every such scalar stays a string.
"""
import os
import pwd
import re
import reprlib
import shlex
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

_MAX_CFG_BYTES = 1 << 20
_LSPCI_TIMEOUT_S = 2.0
_PCI_RE = re.compile(r"^(?:([0-9a-f]{4}):)?([0-9a-f]{2}):([0-9a-f]{2})\.([0-7])$", re.IGNORECASE)
_SMALL_READ = 4096

Kind = Literal["af_packet", "pci", "dummy", "vdev"]


@dataclass(frozen=True)
class PortIface:
    """What is known about one port's interface. `confirmed` is True only when `kernel_name` passed the MAC
    check or the PCI facts were read past the host gate; the UI renders unconfirmed entries muted."""
    port: int
    cfg_entry: str | None
    kind: Kind | None
    kernel_name: str | None
    pci_addr: str | None
    driver: str | None
    vendor_device: str | None
    model: str | None
    confirmed: bool
    note: str | None


class _StringLoader(yaml.SafeLoader):
    """SafeLoader with no implicit int, float, bool or null: `03:00.0`, `on` and `~` stay strings."""


_DROPPED_TAGS = ("tag:yaml.org,2002:int", "tag:yaml.org,2002:float", "tag:yaml.org,2002:bool",
                 "tag:yaml.org,2002:null")
_StringLoader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag not in _DROPPED_TAGS]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _user() -> str:
    """The name of the user this process runs as, or its uid when the passwd database has none."""
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return f"uid {os.geteuid()}"


def read_cfg(path: str, limit: int = _MAX_CFG_BYTES) -> tuple[str | None, str | None]:
    """The cfg as text, or `(None, note)` naming the path for a missing, unreadable, non-regular, over-limit
    or non-text file. A FIFO is refused before any read. An OS error is a note; a path with a NUL byte
    raises ValueError (the caller passes a settings path, not request input)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, f"no config file at {path}"
    except PermissionError as e:                # a root-only cfg is common: say who needs access (RC1-16)
        return None, (f"cannot read {path}: {e.strerror}; trex-web-gui runs as {_user()}, who cannot read it "
                      f"(for example `sudo chmod o+r {path}`)")
    except OSError as e:
        return None, f"cannot read {path}: {e.strerror}"
    try:
        with os.fdopen(fd, "rb") as f:
            mode = os.fstat(f.fileno()).st_mode
            if stat.S_ISDIR(mode):
                return None, f"cannot read {path}: Is a directory"
            if not stat.S_ISREG(mode):
                return None, f"cannot read {path}: not a regular file"
            data = f.read(limit + 1)
    except OSError as e:
        return None, f"cannot read {path}: {e.strerror}"
    if len(data) > limit:
        return None, f"{path} is larger than 1 MiB"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, f"{path} is not a text file"
    if "\x00" in text:
        return None, f"{path} is not a text file"
    return text, None


def _load(cfg_text: str) -> tuple[list[Any], int | None]:
    """The first cfg element's `interfaces` list and its `port_limit` (None when absent, not an int or a
    string of one, or negative). ValueError with a user-facing message for invalid YAML, an alias, a
    top-level map, a first element that is not a map, or no `interfaces` list. Parses events first, so an
    alias is refused before anything expands."""
    try:
        for ev in yaml.parse(cfg_text, Loader=_StringLoader):    # events only: nothing is built or expanded
            if isinstance(ev, yaml.AliasEvent):    # aliases and merge keys expand exponentially in yaml.load
                raise ValueError("the config file uses YAML aliases (*name), which are not supported")
    except (yaml.YAMLError, RecursionError) as e:
        raise ValueError(f"the config file is not valid YAML: {type(e).__name__}") from None
    try:
        doc = yaml.load(cfg_text, Loader=_StringLoader)  # noqa: S506 - a SafeLoader subclass
    except (yaml.YAMLError, RecursionError) as e:
        raise ValueError(f"the config file is not valid YAML: {type(e).__name__}") from None
    if isinstance(doc, dict):
        raise ValueError("the config file is a map; TRex expects a list whose first item has `interfaces`")
    if not isinstance(doc, list) or not doc or not isinstance(doc[0], dict):
        raise ValueError("the config file is not a list whose first item is a map")
    first = doc[0]
    interfaces = first.get("interfaces")
    if not isinstance(interfaces, list):
        raise ValueError("the config file has no `interfaces` list")
    limit: int | None = None
    raw_limit = first.get("port_limit")
    if raw_limit is not None:
        try:
            if isinstance(raw_limit, int) and not isinstance(raw_limit, bool):    # !!int 2
                limit = raw_limit
            else:
                limit = int(raw_limit) if isinstance(raw_limit, str) else None   # no str() of a container
        except ValueError:
            limit = None
        if limit is not None and limit < 0:
            limit = None
    return interfaces, limit


def parse_interfaces(cfg_text: str) -> tuple[list[str], int | None]:
    """`(interfaces, port_limit)` from the cfg's first element. Only string entries are kept: an unquoted
    scalar stays a string, but an explicit tag (`!!int 2`) still builds its type, and a list or map entry is
    dropped rather than `str()`-ed. `ValueError(message)` for a bad file, an alias, a top-level map or no
    `interfaces`. `resolve()` calls `_load` directly; this is the entry point the tests use."""
    interfaces, limit = _load(cfg_text)
    return [i for i in interfaces if isinstance(i, str)], limit


def lspci_models() -> dict[str, str]:
    """`{slot: "Vendor Device name"}` from a single `lspci -mm -D`; `{}` on any failure."""
    try:
        proc = subprocess.run(["lspci", "-mm", "-D"], capture_output=True, text=True,
                              timeout=_LSPCI_TIMEOUT_S, check=False)
    except Exception:       # noqa: BLE001 - missing binary, timeout, undecodable output: all mean "no models"
        return {}
    if proc.returncode != 0:
        return {}
    models: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        try:
            fields = [t for t in shlex.split(line) if not t.startswith("-")]
        except ValueError:
            continue
        if len(fields) >= 4:
            models[fields[0]] = f"{fields[2]} {fields[3]}"
    return models


def _norm_pci(text: str) -> str | None:
    """A PCI address as lower-case `dddd:bb:dd.f` (domain 0000 when omitted), or None when `text` does not
    parse."""
    m = _PCI_RE.match(text.strip())
    if not m:
        return None
    return f"{(m.group(1) or '0000').lower()}:{m.group(2).lower()}:{m.group(3).lower()}.{m.group(4)}"


def _read(path: Path) -> str | None:
    """The first 4096 bytes of the file as stripped UTF-8 text, or None on an OS error or undecodable
    bytes."""
    try:
        with open(path, "rb") as f:
            return f.read(_SMALL_READ).decode("utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None


def _usable_mac(hw_mac: Any) -> str | None:
    """`hw_mac` stripped and lower-cased, or None when it is not a string, empty, `n/a` or all zeros."""
    if not isinstance(hw_mac, str):
        return None
    mac = hw_mac.strip().lower()
    if mac in ("", "n/a") or not mac.replace(":", "").replace("-", "").strip("0"):    # missing, N/A, all zero
        return None
    return mac


def _mac_of(path: Path) -> str | None:
    """The lower-cased text of the address file, or None when it is unreadable or empty."""
    text = _read(path)
    return text.lower() if text else None


def _classify(entry: str) -> Kind:
    """The cfg entry's kind, in check_pdev_vdev_dummy's order: `dummy`, `--vdev` anywhere, no `:` is
    af_packet, else pci."""
    if entry == "dummy":
        return "dummy"
    if "--vdev" in entry:
        return "vdev"
    if ":" not in entry:
        return "af_packet"
    return "pci"


def _join(notes: list[str]) -> str | None:
    """The notes joined with `; `, or None when there are none."""
    return "; ".join(notes) if notes else None


_USERSPACE_DRIVERS = ("vfio-pci", "uio_pci_generic", "igb_uio")     # no kernel interface when bound to one


def _kernel_name_for_pci(root: Path, addr: str, mac: str, driver: str | None = None) -> str | None:
    """The `net/<n>` child of the device whose address is `mac`. A device with no `net/` child, and not bound
    to a userspace driver, falls back to the one physical interface with that MAC; an interface without a
    `device` entry (vlan, bridge, bond, macvlan, veth) never counts in the scan."""
    net = root / "sys/bus/pci/devices" / addr / "net"
    try:
        children = sorted(os.listdir(net))
    except OSError:
        children = []
    if children:        # the device has kernel interfaces: only they can match, never the system-wide scan
        return next((n for n in children if _mac_of(net / n / "address") == mac), None)
    if driver in _USERSPACE_DRIVERS:
        return None
    classdir = root / "sys/class/net"
    try:
        names = sorted(os.listdir(classdir))
    except OSError:
        return None
    hits = [n for n in names
            if (classdir / n / "device").exists() and _mac_of(classdir / n / "address") == mac]
    return hits[0] if len(hits) == 1 else None


_STACKED = ("bonding", "bridge", "brport", "bonding_slave")      # bond/bridge master, bridge/bond member


def _scan_by_mac(root: Path, mac: str) -> str | None:
    """The one interface under `sys/class/net` whose address is `mac`, physical or virtual. `lo` and stacked
    or aggregated interfaces (a `lower_*` entry, `bonding/`, `bridge/`, `brport`, `bonding_slave`) never
    count.
    None on zero or several hits, or when the directory cannot be listed."""
    classdir = root / "sys/class/net"
    try:
        names = sorted(os.listdir(classdir))
    except OSError:
        return None
    hits: list[str] = []
    for n in names:
        if n == "lo":
            continue
        try:
            children = os.listdir(classdir / n)
        except OSError:
            continue
        if any(c.startswith("lower_") or c in _STACKED for c in children):
            continue
        if _mac_of(classdir / n / "address") == mac:
            hits.append(n)
    return hits[0] if len(hits) == 1 else None


def _pci_facts(root: Path, addr: str) -> tuple[str | None, str | None] | None:
    """`(driver, vendor:device ids)` read from the device's sysfs files, each None when missing; None
    overall when the device directory does not exist."""
    dev = root / "sys/bus/pci/devices" / addr
    if not dev.is_dir():
        return None
    driver = None
    uevent = _read(dev / "uevent")
    for line in (uevent or "").splitlines():
        if line.startswith("DRIVER="):
            driver = line[len("DRIVER="):].strip() or None
    vendor, device = _read(dev / "vendor"), _read(dev / "device")
    vd = None
    if vendor and device:
        vd = f"{vendor.removeprefix('0x').lower()}:{device.removeprefix('0x').lower()}"
    return driver, vd


def resolve(cfg_text: str | None, cfg_note: str | None, ports: list[dict[str, Any]], *, server_hostname: str,
            local_hostname: str, root: Path = Path("/"),
            lspci: Callable[[], dict[str, str]] = lspci_models) -> tuple[list[PortIface], str | None]:
    """One `PortIface` per well-formed port, sorted by id (malformed port entries are skipped and noted),
    plus the session note joining everything that went wrong, or None. Reads sysfs under `root` and runs
    lspci at most once, only for a local port whose PCI device exists; makes no RPC and writes nothing."""
    session_notes: list[str] = []
    good = [p for p in ports if isinstance(p, dict) and isinstance(p.get("id"), int)
            and not isinstance(p.get("id"), bool) and isinstance(p.get("info"), dict)]
    if len(good) != len(ports):
        session_notes.append(f"skipped {len(ports) - len(good)} malformed port entries")
    ports = good
    if cfg_note:
        session_notes.append(cfg_note)
    entries: list[Any] = []
    if cfg_text is not None:
        try:
            entries, limit = _load(cfg_text)
        except ValueError as e:
            session_notes.append(str(e))
            entries, limit = [], None
        else:
            if limit is not None and limit < len(entries):
                entries = entries[:limit]
            if len(entries) != len(ports):
                session_notes.append(f"the config lists {len(entries)} interfaces but the server has "
                                     f"{len(ports)} ports: no config entries attached")
                entries = []
    local = server_hostname == local_hostname
    if not local:
        session_notes.append(f"TRex reports host {server_hostname}; this backend runs on {local_hostname}: "
                             "no local lookups")

    models: dict[str, str] | None = None
    seen: dict[str, int] = {}
    out: list[PortIface] = []
    for view in sorted(ports, key=lambda p: p["id"]):
        pid = view["id"]
        info = view["info"]
        notes: list[str] = []
        has_entry = 0 <= pid < len(entries)
        raw = entries[pid] if has_entry else None
        entry: str | None = None
        kind: Kind | None = None
        if has_entry and not isinstance(raw, str):
            notes.append(f"interfaces[{pid}] is a {type(raw).__name__}, not a string")   # no repr(): aliases
        elif isinstance(raw, str):
            entry, kind = raw, _classify(raw)
            key = (_norm_pci(entry) or entry) if kind == "pci" else entry
            if kind != "dummy" and key in seen:
                notes.append(f"{entry} is listed twice in the config (also port {seen[key]})")
            seen.setdefault(key, pid)

        mac = _usable_mac(info.get("hw_mac"))
        server_pci = _norm_pci(str(info.get("pci_addr", "")))
        cfg_pci = _norm_pci(entry) if kind == "pci" and entry is not None else None
        if kind == "pci" and cfg_pci is None:
            notes.append(f"{entry} is not a PCI address")
        pci_addr = server_pci or cfg_pci
        if server_pci and cfg_pci and server_pci != cfg_pci:
            notes.append(f"config says {entry}, server says {server_pci}")

        kernel_name = driver = vendor_device = model = None
        confirmed = False
        if local and kind in ("dummy", "vdev"):
            notes.append(f"{kind} entry: no local lookups")
        elif local:
            if kind == "af_packet":
                assert entry is not None
                if "/" in entry or entry in (".", "..") or "\x00" in entry:
                    notes.append(f"{reprlib.repr(entry)} is not a usable interface name")
                elif mac is None:
                    notes.append(f"{entry} not confirmed: the port has no usable MAC")
                elif _mac_of(root / "sys/class/net" / entry / "address") == mac:
                    kernel_name, confirmed = entry, True
                else:
                    found = _scan_by_mac(root, mac)
                    if found is not None and found != entry:
                        notes.append(f"{entry} not confirmed: MAC {mac} is on {found}")
                    else:
                        notes.append(f"{entry} not confirmed: no such interface with MAC {mac} here")
            elif server_pci is None:
                if kind == "pci":
                    notes.append("server reports no PCI address")
                else:
                    found = _scan_by_mac(root, mac) if mac is not None else None
                    if found is not None:
                        kernel_name, confirmed = found, True
                        notes.append("found by MAC (no config entry)")
                    else:
                        notes.append("no config entry and no PCI address from the server")
            else:
                facts = _pci_facts(root, server_pci)
                if facts is None:
                    notes.append(f"no PCI device {server_pci} on this host")
                else:
                    driver, vendor_device = facts
                    confirmed = True
                    if models is None:
                        models = lspci()
                    model = models.get(server_pci)
                    if mac is not None:
                        kernel_name = _kernel_name_for_pci(root, server_pci, mac, driver)
                    if kernel_name is None:
                        notes.append(f"no kernel interface: bound to {driver}" if driver
                                     else "no kernel interface: not bound to a driver")
        out.append(PortIface(port=pid, cfg_entry=entry, kind=kind, kernel_name=kernel_name, pci_addr=pci_addr,
                             driver=driver, vendor_device=vendor_device, model=model, confirmed=confirmed,
                             note=_join(notes)))
    return out, _join(session_notes)
