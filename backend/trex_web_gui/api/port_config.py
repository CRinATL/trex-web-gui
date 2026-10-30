"""Request bodies of the P3b port commands (Apply, ping, IPv6 scan), validated before any RPC (Global
Constraints); split from api/app.py (project review O2), which keeps the routes."""
import ipaddress
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from trex_web_gui.sessions.port_config import LayerRequest

# Used with fullmatch: `$` would accept a trailing newline.
MAC_RE = re.compile(r"([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}")


def ip_of(v: str, version: int | None = None) -> str:
    """`v` as a normalised IP address string, of IP `version` when one is given; ValueError naming `v`
    otherwise."""
    try:
        a = ipaddress.ip_address(v)
    except ValueError:
        raise ValueError(f"{v!r} is not an IP address") from None
    if version is not None and a.version != version:
        raise ValueError(f"{v!r} is not an IPv{version} address")
    return str(a)


class Ipv6Body(BaseModel):
    enabled: bool
    src: str | None = None         # omitted or null with enabled: the stack picks one (auto)

    @model_validator(mode="after")
    def valid(self) -> "Ipv6Body":
        """Keeps `src`, validated and normalised as IPv6, only when enabled and given; otherwise sets it to
        None."""
        self.src = ip_of(self.src, 6) if self.enabled and self.src else None
        return self


class LayerBody(BaseModel):
    """Validated here, before any RPC (Global Constraints)."""
    port: int = Field(ge=0)
    mode: Literal["L2", "L3"]
    dst_mac: str | None = None
    src_ipv4: str | None = None
    dst_ipv4: str | None = None
    vlan: list[Annotated[int, Field(ge=1, le=4095)]] = Field(default_factory=list, max_length=2)
    ipv6: Ipv6Body | None = None

    @model_validator(mode="after")
    def consistent(self) -> "LayerBody":
        """L2 needs a MAC `dst_mac` (stored lower-case); L3 needs both IPv4 addresses (stored normalised);
        ValueError otherwise."""
        if self.mode == "L2":
            if not self.dst_mac or not MAC_RE.fullmatch(self.dst_mac):
                raise ValueError("L2 mode needs dst_mac as aa:bb:cc:dd:ee:ff")
            self.dst_mac = self.dst_mac.lower()
        else:
            try:
                if not self.src_ipv4 or not self.dst_ipv4:
                    raise ValueError
                self.src_ipv4, self.dst_ipv4 = ip_of(self.src_ipv4, 4), ip_of(self.dst_ipv4, 4)
            except ValueError:
                raise ValueError("Source and destination must both be IPv4") from None
        return self

    def request(self) -> LayerRequest:
        """The session's LayerRequest: mode, addresses, a copy of the VLAN list, and the IPv6 setting as
        (enabled, src) or None."""
        v6 = None if self.ipv6 is None else (self.ipv6.enabled, self.ipv6.src)
        return LayerRequest(self.mode, self.dst_mac, self.src_ipv4, self.dst_ipv4, list(self.vlan), v6)


class PingBody(BaseModel):
    port: int = Field(ge=0)
    dst: str
    count: int = Field(5, ge=1, le=10)
    pkt_size: int = Field(64, ge=64, le=9216)

    @field_validator("dst")
    @classmethod
    def address(cls, v: str) -> str:
        """Validates `dst` as an IPv4 or IPv6 address; returns it normalised."""
        return ip_of(v)


class ScanBody(BaseModel):
    port: int = Field(ge=0)
