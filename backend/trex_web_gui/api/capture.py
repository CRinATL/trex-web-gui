"""Request bodies of the P3c capture routes, validated before any RPC (the api/port_config.py pattern); the
routes stay in api/app.py. `limit` is 1..100 000 (server memory is O(9 KB x limit), spec §6.7)."""
import base64
import binascii
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PortList = list[Annotated[int, Field(ge=0)]]


class CaptureBody(BaseModel):
    tx: PortList = Field(default_factory=list, max_length=64)
    rx: PortList = Field(default_factory=list, max_length=64)
    bpf: str = Field("", max_length=1000)        # passed through uncompiled: the server is the validator

    @field_validator("tx", "rx")
    @classmethod
    def unique(cls, v: list[int]) -> list[int]:
        """Field validator: rejects a port repeated within tx or within rx."""
        if len(set(v)) != len(v):
            raise ValueError("ports must be unique within tx and within rx")
        return v

    @model_validator(mode="after")
    def some_port(self) -> "CaptureBody":
        """Rejects a body that names no port in tx or rx."""
        if not self.tx and not self.rx:
            raise ValueError("choose at least one port in tx or rx")
        return self


class MonitorBody(CaptureBody):
    limit: int = Field(100, ge=1, le=100_000)


class RecorderBody(CaptureBody):
    limit: int = Field(1000, ge=1, le=100_000)


class PacketBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binary: str = Field(min_length=1, max_length=200_000)      # base64 of one frame

    @field_validator("binary")
    @classmethod
    def base64_text(cls, v: str) -> str:
        """Rejects `binary` that is not strict base64; returns it still encoded."""
        try:
            base64.b64decode(v, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("binary is not valid base64") from None
        return v
