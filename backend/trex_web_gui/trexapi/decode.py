"""Packet decoding for the capture viewer. Scapy executor only (scapy_exec.py): Scapy_service swaps
sys.stdout and holds instance state, so every call into it runs on the one thread the executor owns.

`summary` never raises out of a batch: a frame that fails decodes as proto "?" with the exception text in
`info`. A frame Scapy dissects without error keeps whatever it dissected (30 zero bytes dissect as Dot3, not
Ether, in the vendored Scapy 2.4.3). The recorded udp tree has two Raw layers; that is Scapy's.

An LLDP frame (EtherType 0x88cc, tagged or not) is labelled "LLDP": Scapy is not told about LLDP (lldp.py says
why), so it would name the frame after its Ether or Dot1Q layer."""
import base64
from typing import Any, NamedTuple

from . import lldp
from .loader import load

_service: Any = None
_SKIP_PROTO = ("Raw", "Padding", "ICMPv6NDOpt")      # layers that are payload or options, not the protocol
# A row names the headers at the frame's start, so only these bytes are dissected: whole, a zero-filled jumbo
# IPv4 frame took 0.45 s against 0.10 s for its first 2048, and frames arrive from the test network unasked.
# The packet worker's list rows use the same number (packet_worker.SUMMARY_BYTES).
SUMMARY_BYTES = 2048


class Summary(NamedTuple):
    src: str
    dst: str
    proto: str
    info: str


def _construct() -> Any:
    """Load the vendored trex package and build a new Scapy_service (no singleton handling).
    Scapy executor only."""
    load()
    from trex.scapy_server.scapy_service import Scapy_service
    return Scapy_service()


def service() -> Any:
    """The Scapy_service singleton, constructed on first use (the app lifespan does it through
    scapy_exec.warm_up before any session connects)."""
    global _service
    if _service is None:
        _service = _construct()
    return _service


def _addresses(pkt: Any) -> tuple[str, str]:
    """(src, dst) of the first IP, IPv6 or ARP layer, else of the Ether or Dot3 header, else empty."""
    from scapy.all import ARP, IP, Dot3, Ether, IPv6
    for layer, s, d in ((IP, "src", "dst"), (IPv6, "src", "dst"), (ARP, "psrc", "pdst")):
        found = pkt.getlayer(layer)
        if found is not None:
            return str(getattr(found, s)), str(getattr(found, d))
    for layer in (Ether, Dot3):
        found = pkt.getlayer(layer)
        if found is not None:
            return str(found.src), str(found.dst)
    return "", ""


def summary(raw: bytes) -> Summary:
    """The frame's src, dst, protocol and info, from its first SUMMARY_BYTES (the LLDP check reads them all);
    a decode failure gives proto "?" and the error text. Scapy executor only."""
    try:
        load()
        from scapy.all import Dot1Q, Ether
        pkt = Ether(raw[:SUMMARY_BYTES])
        names = [type(layer).__name__ for layer in pkt.iterpayloads()]
        proto = "LLDP" if lldp.is_lldp(raw) else next(
            (n for n in reversed(names) if not n.startswith(_SKIP_PROTO)), names[0])
        info = str(pkt.summary())
        tag = pkt.getlayer(Dot1Q)
        if tag is not None:
            info = info.replace("Dot1Q", f"802.1Q vlan {tag.vlan}", 1)
        src, dst = _addresses(pkt)
        return Summary(src, dst, proto, info)
    except Exception as e:
        return Summary("", "", "?", f"{type(e).__name__}: {e}")


def tree(raw: bytes) -> dict[str, Any]:
    """The Studio layer tree, the singleton's own reconstruct_pkt with an empty model (Scapy executor only).
    Unlike summary(), a frame Scapy cannot dissect raises (5 bytes: struct.error); the caller catches it.
    The recorded fixtures are made with it; the capture route runs the packet worker's `tree` op instead,
    which has a timeout and a memory limit (an unbounded frame here would stall or exhaust the backend)."""
    b64 = base64.b64encode(raw).decode("ascii")
    result: dict[str, Any] = service().reconstruct_pkt(None, b64, [])
    return result
