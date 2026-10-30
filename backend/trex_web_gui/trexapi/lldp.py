"""LLDP neighbours from captured frames (P3c Task 11): decode-only, IEEE 802.1AB TLVs parsed with `struct`.

The vendored Scapy cannot use its own scapy/contrib/lldp.py (Task 11 ruling, superseding spec §1's "decoded
with the bundled scapy/contrib/lldp.py"). The fork adds a `_length` slot to every Packet
(`_vendor/external_libs/scapy-2.4.3/scapy/packet.py:80`), which shadows the `_length` field each TLV reads in
its `length_from`. So a string port ID swallows every TLV after it, and strict mode rejects a valid frame. Its
`do_build(self, result)` (`_vendor/external_libs/scapy-2.4.3/scapy/packet.py:652`) also breaks the contrib's
`do_build(self)`. This module never imports the contrib, and nothing loads it, so its global `bind_layers`
never changes how other code dissects 0x88cc.

`parse` runs on the scapy executor with the rest of a batch's decoding (sessions/capture.py `decode_batch`).
It needs no Scapy. Keeping it there just keeps the batch work off the loop thread in one job. It never raises
and logs nothing per frame. A frame that is not LLDP gives None, as does an LLDPDU whose TLV framing is broken
(truncated, a length past the end, a dangling byte) or whose mandatory TLVs are bad (chassis ID, port ID and
TTL missing, out of order, malformed, or one of them again later). A malformed optional TLV is skipped and the
rest of the LLDPDU kept (802.1AB 9.2.7.7), so that field is empty.

A neighbour is one row per (port, VLAN, chassis ID, port ID): heard tagged and untagged on one port, it is two
rows, because each is a separate LLDP agent on the wire."""
import ipaddress
import struct
from collections.abc import Iterable
from typing import TypedDict

ETHERTYPE = 0x88CC
VLAN_TPIDS = (0x8100, 0x88A8, 0x9100)
BPF = "ether proto 0x88cc or (vlan and ether proto 0x88cc)"
NOTE = ("Some NICs consume LLDP in firmware (Intel X710/XL710 under i40e is the known case), so a neighbour "
        "that sends LLDP may never appear here. Unverified on hardware: the test guest has no LLDP speaker.")

CHASSIS_SUBTYPES = {1: "chassis component", 2: "interface alias", 3: "port component", 4: "MAC address",
                    5: "network address", 6: "interface name", 7: "locally assigned"}
PORT_SUBTYPES = {1: "interface alias", 2: "port component", 3: "MAC address", 4: "network address",
                 5: "interface name", 6: "agent circuit ID", 7: "locally assigned"}
MAC_SUBTYPE = {"chassis": 4, "port": 3}
NETWORK_SUBTYPE = {"chassis": 5, "port": 4}
CAPABILITIES = ("other", "repeater", "bridge", "WLAN access point", "router", "telephone",
                "DOCSIS cable device", "station only", "C-VLAN component", "S-VLAN component",
                "two-port MAC relay")


class LldpNeighbour(TypedDict):
    port: int                       # the TRex port the frame was captured on
    vlan: int | None                # the outer 802.1Q tag's VLAN, or None when untagged
    src: str                        # the frame's source MAC
    chassis_id: str
    chassis_id_subtype: str
    port_id: str
    port_id_subtype: str
    ttl: int                        # seconds; 0 is the shutdown LLDPDU (the entry is removed)
    system_name: str | None
    system_description: str | None
    port_description: str | None
    management_addresses: list[str]
    capabilities: list[str]         # the enabled ones
    last_seen: float                # the backend's time.time() when the batch was decoded
    expires: float                  # last_seen + ttl


Key = tuple[int, int, str, str]          # (port, VLAN or -1 when untagged, chassis ID, port ID)


class _Malformed(Exception):
    pass


def _mac(b: bytes) -> str:
    """Bytes as a lower-case colon-separated MAC."""
    return ":".join(f"{x:02x}" for x in b)


def _text(b: bytes) -> str:
    """The bytes without trailing NULs, decoded as UTF-8 with bad bytes replaced."""
    return b.rstrip(b"\x00").decode("utf-8", errors="replace")


def _address(family: int, b: bytes) -> str:
    """An IANA address-family-tagged address: IPv4 and IPv6 as addresses, a 6-byte 802 address as a MAC,
    anything else as hex."""
    if (family, len(b)) in ((1, 4), (2, 16)):
        return str(ipaddress.ip_address(b))
    if family == 6 and len(b) == 6:
        return _mac(b)
    return b.hex()


def _id(kind: str, value: bytes) -> tuple[str, str]:
    """Chassis or port ID TLV value -> (id, subtype name): a subtype byte and at least one byte of ID."""
    if len(value) < 2:
        raise _Malformed
    subtype, body = value[0], value[1:]
    names = CHASSIS_SUBTYPES if kind == "chassis" else PORT_SUBTYPES
    if subtype == MAC_SUBTYPE[kind] and len(body) == 6:
        shown = _mac(body)
    elif subtype == NETWORK_SUBTYPE[kind] and len(body) >= 2:
        shown = _address(body[0], body[1:])
    else:
        text = _text(body)
        shown = text if text.isprintable() and "�" not in text else body.hex()
    return shown, names.get(subtype, f"reserved ({subtype})")


def _management_address(value: bytes) -> str:
    """A management address TLV value as address text; _Malformed on a bad length."""
    if len(value) < 2:
        raise _Malformed
    n = value[0]                    # the length of subtype + address (802.1AB: 2..32)
    # then the interface numbering subtype (1), the interface number (4), the OID string length (1), the OID
    oid_at = 1 + n + 5
    if not 2 <= n <= 32 or oid_at + 1 > len(value) or oid_at + 1 + value[oid_at] > len(value):
        raise _Malformed
    return _address(value[1], value[2:1 + n])


def _capabilities(value: bytes) -> list[str]:
    """Names of the enabled bits of a system capabilities TLV value; _Malformed unless it is 4 bytes."""
    if len(value) != 4:
        raise _Malformed
    _system, enabled = struct.unpack("!HH", value)
    return [name for bit, name in enumerate(CAPABILITIES) if enabled & (1 << bit)]


def _payload(raw: bytes) -> tuple[int, int | None] | None:
    """(offset of the LLDPDU, outer VLAN) when the EtherType after any VLAN tags is 0x88cc, else None."""
    pos, vlan = 12, None
    while pos + 2 <= len(raw):
        etype = struct.unpack_from("!H", raw, pos)[0]
        if etype == ETHERTYPE:
            return pos + 2, vlan
        if etype not in VLAN_TPIDS or pos + 4 > len(raw):
            return None
        if vlan is None:
            vlan = struct.unpack_from("!H", raw, pos + 2)[0] & 0x0FFF
        pos += 4
    return None


def is_lldp(raw: bytes) -> bool:
    """The EtherType (after any VLAN tags) is LLDP's; says nothing about whether the LLDPDU is well formed."""
    return _payload(raw) is not None


def _tlvs(pdu: bytes) -> list[tuple[int, bytes]]:
    """Every TLV up to End (or the end of the data). A length past the end is malformed."""
    out: list[tuple[int, bytes]] = []
    pos = 0
    while pos + 2 <= len(pdu):
        head = struct.unpack_from("!H", pdu, pos)[0]
        t, n = head >> 9, head & 0x01FF
        if t == 0:
            return out
        if pos + 2 + n > len(pdu):
            raise _Malformed
        out.append((t, pdu[pos + 2:pos + 2 + n]))
        pos += 2 + n
    if pos != len(pdu):             # one dangling byte: a TLV header cut in half
        raise _Malformed
    return out


def parse(raw: bytes, port: int, now: float) -> LldpNeighbour | None:
    """The neighbour a frame captured on port at time now announces; None when it is not LLDP or its TLVs
    are bad (see the module docstring)."""
    found = _payload(raw)
    if found is None:
        return None
    start, vlan = found
    try:
        tlvs = _tlvs(raw[start:])
        if [t for t, _ in tlvs[:3]] != [1, 2, 3] or len(tlvs[2][1]) != 2:
            return None             # 802.1AB: chassis ID, port ID and TTL come first, in that order
        chassis_id, chassis_sub = _id("chassis", tlvs[0][1])
        port_id, port_sub = _id("port", tlvs[1][1])
        ttl = struct.unpack("!H", tlvs[2][1])[0]
    except (_Malformed, ValueError, struct.error):
        return None
    texts: dict[int, str] = {}
    addresses: list[str] = []
    capabilities: list[str] | None = None
    for t, value in tlvs[3:]:
        if t in (1, 2, 3):
            return None             # a mandatory TLV twice
        try:
            if t in (4, 5, 6):
                texts.setdefault(t, _text(value))
            elif t == 7 and capabilities is None:
                capabilities = _capabilities(value)
            elif t == 8:
                addresses.append(_management_address(value))
        except (_Malformed, ValueError, struct.error):
            continue                # 802.1AB 9.2.7.7: discard this optional TLV, keep the rest
    return {"port": port, "vlan": vlan, "src": _mac(raw[6:12]),
            "chassis_id": chassis_id, "chassis_id_subtype": chassis_sub,
            "port_id": port_id, "port_id_subtype": port_sub, "ttl": ttl,
            "system_name": texts.get(5), "system_description": texts.get(6), "port_description": texts.get(4),
            "management_addresses": addresses, "capabilities": capabilities or [],
            "last_seen": now, "expires": now + ttl}


def key(n: LldpNeighbour) -> Key:
    """The table key of a neighbour: (port, VLAN or -1, chassis ID, port ID)."""
    return (n["port"], -1 if n["vlan"] is None else n["vlan"], n["chassis_id"], n["port_id"])


def merge(table: dict[Key, LldpNeighbour], seen: Iterable[LldpNeighbour]) -> None:
    """Newer frames replace older ones; a TTL of 0 (the shutdown LLDPDU) removes the neighbour."""
    for n in seen:
        if n["ttl"] == 0:
            table.pop(key(n), None)
        else:
            table[key(n)] = n


def current(table: dict[Key, LldpNeighbour], now: float) -> list[LldpNeighbour]:
    """Drop every entry whose TTL ran out (now >= last_seen + ttl); the rest, sorted by the key: port, VLAN,
    chassis ID, port ID."""
    for k in [k for k, n in table.items() if now >= n["expires"]]:
        del table[k]
    return [table[k] for k in sorted(table)]
