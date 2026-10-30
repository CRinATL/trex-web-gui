"""Packet presets (P4b plan Task 11): four forms that replace the legacy Simple Mode, each building a packet
document (`.trp`) with its Field Engine instructions, and, for the flood, the stream's MAC-mode bits.

Pure: nothing here imports `trex` or `scapy`, and nothing is built or evaluated; the editor builds the
returned document through the engine like any other. Every Field Engine parameter value is a string, as in a
`.trp` file.
Nothing emits `split_by_var` (plan D2): each flow var keeps `split_to_cores`, which defaults to true."""
import base64
import ipaddress
import re
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from trex_web_gui.studio.packetdoc import DocField, DocLayer, FeInstruction, PacketDoc, empty_doc

PRESETS = ("flood", "ip_sweep", "frame_size_sweep", "vlan")
NAMES = {"flood": "Flood", "ip_sweep": "IP sweep", "frame_size_sweep": "Frame-size sweep", "vlan": "VLAN"}
FRAME_MIN = 64                           # an Ethernet frame with its FCS
FRAME_MAX = 9238                         # the longest stream packet P4a accepts
FCS = 4                                  # bytes the frame size counts and the packet does not
COUNT_MAX = 16777216                     # addresses one IP sweep covers
MAC_COUNT_MAX = 65535
LOW32 = 0xFFFFFFFF
L4_TYPE = {"udp": "11", "tcp": "13"}     # trex_stl_packet_builder_scapy.py:214-215 "L4_TYPE_UDP = 11"
MAC_RE = re.compile(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}")
PAYLOAD_RE = re.compile(r"[\x20-\x7e]{1,64}")
Op = Literal["inc", "dec", "random"]


class PresetError(ValueError):
    """Parameters a preset refuses. `where` names the parameter (`src.count`); `message` is the reason."""

    def __init__(self, where: str, message: str) -> None:
        """Keep `where` and `message`; the text is `"<where>: <message>"`."""
        super().__init__(f"{where}: {message}")
        self.where = where
        self.message = message


class PresetNotFound(KeyError):
    """A preset id that is not in PRESETS."""


class PresetResult(TypedDict):
    doc: PacketDoc
    flags: int | None


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _ipv4(v: str) -> str:
    """`v` when it is a dotted-quad IPv4 address; ValueError otherwise."""
    try:
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", v):
            return str(ipaddress.IPv4Address(v))
    except ipaddress.AddressValueError:
        pass
    raise ValueError(f"{v!r} is not an IPv4 address")


class Mac(_Model):
    """A source or destination MAC: left to the packet, left to the TRex configuration, or stepped."""

    mode: Literal["packet", "trex_config", "inc", "dec"] = "trex_config"
    value: str = "00:00:00:00:00:01"
    count: int = Field(default=1, ge=1, le=MAC_COUNT_MAX)
    step: int = Field(default=1, ge=1, le=LOW32)

    @field_validator("value")
    @classmethod
    def _mac(cls, v: str) -> str:
        """Six colon-separated hex pairs."""
        if not MAC_RE.fullmatch(v):
            raise ValueError(f"{v!r} is not a MAC address (aa:bb:cc:dd:ee:ff)")
        return v.lower()

    @model_validator(mode="after")
    def _fits(self) -> "Mac":
        """A stepped MAC stays inside the low 32 bits it varies."""
        low = int(self.value.replace(":", ""), 16) & LOW32
        span = (self.count - 1) * self.step
        if self.mode == "inc" and low + span > LOW32:
            raise ValueError("the range runs past the low 32 bits of the address")
        if self.mode == "dec" and low - span < 0:
            raise ValueError("the range runs below zero in the low 32 bits of the address")
        return self

    def low32(self) -> int:
        """The low 32 bits of the address."""
        return int(self.value.replace(":", ""), 16) & LOW32


class Frame(_Model):
    """The frame size, FCS included: one size, or a range stepped through or drawn from at random."""

    mode: Literal["fixed", "inc", "dec", "random"] = "fixed"
    min: int = Field(default=FRAME_MIN, ge=FRAME_MIN, le=FRAME_MAX)
    max: int = Field(default=1518, ge=FRAME_MIN, le=FRAME_MAX)

    @model_validator(mode="after")
    def _ordered(self) -> "Frame":
        """min is not above max."""
        if self.min > self.max:
            raise ValueError("min is above max")
        return self


class _Payload(_Model):
    payload: str = "x"

    @field_validator("payload")
    @classmethod
    def _ascii(cls, v: str) -> str:
        """1-64 printable ASCII characters."""
        if not PAYLOAD_RE.fullmatch(v):
            raise ValueError("must be 1-64 printable ASCII characters")
        return v


class Flood(_Payload):
    """A stream of one UDP or TCP packet, its MACs, ports and size optionally varying."""

    l4: Literal["udp", "tcp"] = "udp"
    src_mac: Mac = Field(default_factory=lambda: Mac(value="00:00:00:00:00:01"))
    dst_mac: Mac = Field(default_factory=lambda: Mac(value="00:00:00:00:00:02"))
    src_ip: str = "16.0.0.1"
    dst_ip: str = "48.0.0.1"
    sport: int = Field(default=1025, ge=1, le=65535)
    dport: int = Field(default=12, ge=1, le=65535)
    frame: Frame = Field(default_factory=lambda: Frame())

    @field_validator("src_ip", "dst_ip")
    @classmethod
    def _ip(cls, v: str) -> str:
        """A dotted-quad IPv4 address."""
        return _ipv4(v)


class Sweep(_Model):
    """One address swept: from `start` by `step`, over `count` addresses or up to `end` (exactly one)."""

    start: str
    step: int = Field(default=1, ge=1, le=LOW32)
    op: Op = "inc"
    count: int | None = Field(default=None, ge=1, le=COUNT_MAX)
    end: str | None = None

    @field_validator("start", "end")
    @classmethod
    def _ip(cls, v: str | None) -> str | None:
        """A dotted-quad IPv4 address."""
        return None if v is None else _ipv4(v)

    @model_validator(mode="after")
    def _one_extent(self) -> "Sweep":
        """Exactly one of count and end, and the whole range inside IPv4."""
        if (self.count is None) == (self.end is None):
            raise ValueError("give exactly one of count and end")
        n = self.total()
        if n > COUNT_MAX:
            raise ValueError(f"covers {n} addresses; at most {COUNT_MAX}")
        first = int(ipaddress.IPv4Address(self.start))
        if self.op == "dec":
            if first - (n - 1) * self.step < 0:
                raise ValueError("the range runs below 0.0.0.0")
        elif first + (n - 1) * self.step > LOW32:
            raise ValueError("the range runs past 255.255.255.255")
        return self

    def total(self) -> int:
        """How many addresses: `count`, or |end - start| / step + 1 rounded down. With op dec the range
        counts down from `start`, so `end` may not be above it; otherwise `end` may not be below it."""
        if self.count is not None:
            return self.count
        assert self.end is not None
        span = int(ipaddress.IPv4Address(self.end)) - int(ipaddress.IPv4Address(self.start))
        if self.op == "dec":
            span = -span
            if span < 0:
                raise ValueError("end is above start (dec counts down from start)")
        elif span < 0:
            raise ValueError("end is below start")
        return span // self.step + 1

    def bounds(self) -> tuple[str, str]:
        """(min_value, max_value) of the range. TRex's dec starts at max_value, so a dec sweep has
        max_value = start and counts down to start - (total - 1) * step; the others start at min_value."""
        first = int(ipaddress.IPv4Address(self.start))
        reach = (self.total() - 1) * self.step
        if self.op == "dec":
            return str(ipaddress.IPv4Address(first - reach)), self.start
        return self.start, str(ipaddress.IPv4Address(first + reach))


class IpSweep(_Model):
    """Source, destination or both IPv4 addresses stepped through a range."""

    which: Literal["src", "dst", "both"] = "both"
    src: Sweep | None = None
    dst: Sweep | None = None
    checksum: Literal["ipv4", "hw"] = "ipv4"

    @model_validator(mode="after")
    def _sides(self) -> "IpSweep":
        """The swept sides get their defaults; a side that is not swept may not be given."""
        for side, start in (("src", "16.0.0.1"), ("dst", "48.0.0.1")):
            used = self.which in (side, "both")
            given = getattr(self, side)
            if used and given is None:
                setattr(self, side, Sweep(start=start, count=254))
            elif not used and given is not None:
                raise ValueError(f"{side} is given but which is {self.which}")
        return self


class FrameSizeSweep(_Model):
    """The frame size stepped through, or drawn at random from, a range."""

    min: int = Field(default=FRAME_MIN, ge=FRAME_MIN, le=FRAME_MAX)
    max: int = Field(default=1518, ge=FRAME_MIN, le=FRAME_MAX)
    step: int = Field(default=1, ge=1, le=FRAME_MAX)
    op: Op = "inc"

    @model_validator(mode="after")
    def _ordered(self) -> "FrameSizeSweep":
        """min is not above max."""
        if self.min > self.max:
            raise ValueError("min is above max")
        return self


class Tag(_Model):
    """One 802.1Q tag."""

    vlan: int = Field(ge=1, le=4094)
    prio: int = Field(default=0, ge=0, le=7)


class Vlan(_Model):
    """One or two stacked 802.1Q tags on a UDP packet."""

    tags: list[Tag] = Field(default_factory=lambda: [Tag(vlan=100, prio=0)], min_length=1, max_length=2)


MODELS: dict[str, type[_Model]] = {"flood": Flood, "ip_sweep": IpSweep, "frame_size_sweep": FrameSizeSweep,
                                   "vlan": Vlan}


def _ins(ident: str, **parameters: str) -> FeInstruction:
    """A Field Engine instruction; every parameter value is a string."""
    return {"id": ident, "parameters": parameters}


def _fields(**values: Any) -> list[DocField]:
    """Layer fields `{id, value}` for the given values, in order."""
    return [{"id": k, "value": v} for k, v in values.items()]


def _payload(text: str, total_size: int) -> DocLayer:
    """The Raw layer: `text` repeated to a frame of `total_size` bytes with its FCS."""
    return {"id": "Raw", "fields": _fields(load={
        "vtype": "BYTES", "generate": "template",
        "template_base64": base64.b64encode(text.encode("ascii")).decode("ascii"), "total_size": total_size})}


def _frame_instructions(low: int, high: int, step: int, op: str, udp: bool) -> list[FeInstruction]:
    """A varying frame: a 2-byte `pkt_len` between the frame sizes without their FCS, the trim to it, and
    the IP (and UDP) length fields rewritten from it."""
    out = [_ins("STLVmFlowVar", name="pkt_len", min_value=str(low - FCS), max_value=str(high - FCS), size="2",
                op=op, step=str(step)),
           _ins("STLVmTrimPktSize", fv_name="pkt_len"),
           _ins("STLVmWrFlowVar", fv_name="pkt_len", pkt_offset="IP.len", add_val="-14")]
    if udp:
        out.append(_ins("STLVmWrFlowVar", fv_name="pkt_len", pkt_offset="UDP.len", add_val="-34"))
    return out


def _mac_instructions(side: str, mac: Mac) -> list[FeInstruction]:
    """A MAC stepped over its low 32 bits: the flow var, and its write at the address plus two bytes."""
    span = (mac.count - 1) * mac.step
    low, high = (mac.low32(), mac.low32() + span) if mac.mode == "inc" else (mac.low32() - span, mac.low32())
    name = f"mac_{side}"
    return [_ins("STLVmFlowVar", name=name, min_value=str(low), max_value=str(high), size="4", op=mac.mode,
                 step=str(mac.step)),
            _ins("STLVmWrFlowVar", fv_name=name, pkt_offset=f"Ether.{side}", offset_fixup="2")]


def _flags(src: Mac, dst: Mac) -> int:
    """The stream's MAC-mode bits (B2.8): bit 0, the source MAC from the packet; bits 1-2, the destination,
    0 from the TRex configuration and 1 from the packet."""
    return (src.mode != "trex_config") | ((dst.mode != "trex_config") << 1)


def _l4_fix(l4: str) -> FeInstruction:
    """The hardware checksum fix for UDP or TCP, always the last instruction."""
    return _ins("STLVmFixChecksumHw", l3_offset="IP", l4_offset=l4.upper(), l4_type=L4_TYPE[l4])


def _udp_tcp_doc(l4: str, src_mac: Mac | None, dst_mac: Mac | None, src_ip: str, dst_ip: str, sport: int,
                 dport: int, payload: str, size: int, instructions: list[FeInstruction]) -> PacketDoc:
    """Ether / IP / UDP or TCP / Raw with the given addresses and Field Engine instructions."""
    ether = _fields(**{k: m.value for k, m in (("src", src_mac), ("dst", dst_mac))
                       if m is not None and m.mode != "trex_config"})
    doc = empty_doc()
    doc["packet"] = [{"id": "Ether", "fields": ether},
                     {"id": "IP", "fields": _fields(src=src_ip, dst=dst_ip)},
                     {"id": l4.upper(), "fields": _fields(sport=sport, dport=dport)}, _payload(payload, size)]
    doc["fePrarameters"] = {}
    doc["feInstructions"] = instructions
    return doc


def _flood(p: Flood) -> PresetResult:
    """The flood preset."""
    varying = p.frame.mode != "fixed"
    instructions = (_frame_instructions(p.frame.min, p.frame.max, 1, p.frame.mode, p.l4 == "udp")
                    if varying else [])
    for side, mac in (("src", p.src_mac), ("dst", p.dst_mac)):
        if mac.mode in ("inc", "dec"):
            instructions += _mac_instructions(side, mac)
    instructions.append(_l4_fix(p.l4))
    size = p.frame.max if varying else p.frame.min
    doc = _udp_tcp_doc(p.l4, p.src_mac, p.dst_mac, p.src_ip, p.dst_ip, p.sport, p.dport, p.payload, size,
                       instructions)
    return {"doc": doc, "flags": _flags(p.src_mac, p.dst_mac)}


def _ip_sweep(p: IpSweep) -> PresetResult:
    """The IP sweep preset."""
    instructions: list[FeInstruction] = []
    ips = {"src": "16.0.0.1", "dst": "48.0.0.1"}
    for side in ("src", "dst"):
        sweep: Sweep | None = getattr(p, side)
        if sweep is None:
            continue
        ips[side] = sweep.start
        name = f"ip_{side}"
        low, high = sweep.bounds()
        instructions += [_ins("STLVmFlowVar", name=name, min_value=low, max_value=high,
                              size="4", op=sweep.op, step=str(sweep.step)),
                         _ins("STLVmWrFlowVar", fv_name=name, pkt_offset=f"IP.{side}")]
    instructions.append(_ins("STLVmFixIpv4", offset="IP") if p.checksum == "ipv4" else _l4_fix("udp"))
    doc = _udp_tcp_doc("udp", None, None, ips["src"], ips["dst"], 1025, 12, "x", FRAME_MIN, instructions)
    return {"doc": doc, "flags": None}


def _frame_size_sweep(p: FrameSizeSweep) -> PresetResult:
    """The frame-size sweep preset."""
    instructions = [*_frame_instructions(p.min, p.max, p.step, p.op, True), _l4_fix("udp")]
    doc = _udp_tcp_doc("udp", None, None, "16.0.0.1", "48.0.0.1", 1025, 12, "x", p.max, instructions)
    return {"doc": doc, "flags": None}


def _vlan(p: Vlan) -> PresetResult:
    """The VLAN preset: Ether / Dot1Q x tags / IP / UDP, no Field Engine."""
    doc = empty_doc()
    tags: list[DocLayer] = [{"id": "Dot1Q", "fields": _fields(vlan=t.vlan, prio=t.prio)} for t in p.tags]
    doc["packet"] = [{"id": "Ether", "fields": []}, *tags,
                     {"id": "IP", "fields": _fields(src="16.0.0.1", dst="48.0.0.1")},
                     {"id": "UDP", "fields": _fields(sport=1025, dport=12)}]
    doc["fePrarameters"] = {}
    return {"doc": doc, "flags": None}


def _where_of(e: ValidationError) -> PresetError:
    """The first pydantic error as a PresetError naming its parameter."""
    first = e.errors()[0]
    return PresetError(".".join(str(x) for x in first["loc"]) or "params",
                       str(first["msg"]).removeprefix("Value error, "))


def _model_of(preset_id: str, params: object) -> _Model:
    """The preset's parameter model over `params` (an object; absent parameters take their defaults)."""
    if preset_id not in PRESETS:
        raise PresetNotFound(preset_id)
    if not isinstance(params, dict):
        raise PresetError("params", "must be an object")
    try:
        return MODELS[preset_id].model_validate(params)
    except ValidationError as e:
        raise _where_of(e) from None


def defaults(preset_id: str) -> dict[str, Any]:
    """The parameters a form starts from: the preset's model over no parameters, as plain JSON values."""
    return _model_of(preset_id, {}).model_dump(exclude_none=True)


def preset(preset_id: str, params: object) -> PresetResult:
    """The document and MAC-mode flags of preset `preset_id` over `params`. PresetNotFound for an unknown
    id; PresetError for parameters the preset's model refuses."""
    model = _model_of(preset_id, params)
    if isinstance(model, Flood):
        return _flood(model)
    if isinstance(model, IpSweep):
        return _ip_sweep(model)
    if isinstance(model, FrameSizeSweep):
        return _frame_size_sweep(model)
    assert isinstance(model, Vlan)
    return _vlan(model)
