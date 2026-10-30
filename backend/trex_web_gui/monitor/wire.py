"""Bounded decoding of TRex PUB frames. Every decompression in the Monitor goes through `inflate`, which
never allocates more than `limit` bytes however hostile the frame."""
import json
import struct
import zlib
from dataclasses import dataclass
from typing import Literal

MAGIC = 0xABE85CEA
MAX_FRAME_BYTES = 4 * 1024 * 1024
MAXMSGSIZE = MAX_FRAME_BYTES + 65536


class Undecodable(Exception):
    """A frame that cannot be decoded; `reason` says why."""

    def __init__(self, reason: Literal["oversize", "zlib", "utf8", "json", "shape"]) -> None:
        """Keep `reason` and use it as the message."""
        super().__init__(reason)
        self.reason = reason


def inflate(raw: bytes, limit: int) -> bytes:
    """The body of `raw` in at most `limit` bytes. A magic-prefixed body is zlib-decompressed, refused when
    its length field exceeds `limit`, when it inflates past `limit` or when its zlib stream ends early; any
    other body is returned as is, refused when longer than `limit`. Undecodable("oversize") or ("zlib")
    otherwise."""
    if len(raw) >= 8 and struct.unpack(">I", raw[:4])[0] == MAGIC:
        if struct.unpack(">I", raw[4:8])[0] > limit:
            raise Undecodable("oversize")
        d = zlib.decompressobj()
        try:
            out = d.decompress(raw[8:], limit + 1)
        except zlib.error:
            raise Undecodable("zlib") from None
        if len(out) > limit:
            raise Undecodable("oversize")
        if not d.eof:                       # a stream cut short inflates to a prefix without raising
            raise Undecodable("zlib")
        return out
    if len(raw) > limit:
        raise Undecodable("oversize")
    return raw


@dataclass(frozen=True)
class Frame:
    """One publication: its `name`, its `data` and the length of the message on the wire."""
    name: str
    data: object
    wire_len: int


def decode_frame(raw: bytes) -> Frame | None:
    """None for the `{}` trigger; a Frame for an object with a string `name`; Undecodable otherwise."""
    body = inflate(raw, MAX_FRAME_BYTES)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise Undecodable("utf8") from None
    try:
        obj = json.loads(text)
    except (ValueError, RecursionError):
        raise Undecodable("json") from None
    if not isinstance(obj, dict):
        raise Undecodable("shape")
    if not obj:
        return None
    name = obj.get("name")
    if not isinstance(name, str):
        raise Undecodable("shape")
    return Frame(name, obj.get("data"), len(raw))
