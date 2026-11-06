"""The options codec of the packet editor: IP and TCP option bytes to named rows and back.

Worker-only, like `packet_worker.py`: Scapy is imported inside the functions, after
`trex_web_gui.trexapi.loader.load()`. The kinds come from Scapy's `IPOption_*` classes and `TCPOptions`,
but the walk, the coercion and the encoding are done here with `struct`: the service's `m2i` is not usable (it
reads a TCP length below 2 lossily and every zero after End of Options as one more EOL), and nothing a user
typed reaches a Scapy field's `h2i` or constructor. Each value is coerced to its kind first (`BadOption`)."""
import base64
import binascii
import copy
import functools
import ipaddress
import re
import struct
from typing import Any, Literal, NotRequired, TypedDict

OptionLayer = Literal["IP", "TCP"]
MAX_OPTION_BYTES = 40
# An option's value after its type and length bytes: 40 less those two.
MAX_VALUE_BYTES = MAX_OPTION_BYTES - 2
IP_ID_FIELDS = ("copy_flag", "optclass", "option", "length")
_HEX = re.compile(r"[0-9a-fA-F]*")


class OptionsTooLong(ValueError):
    """The options (rows, then padding) pass the 40 bytes an IP or TCP header holds."""


class BadOption(ValueError):
    """An unknown name, or a missing, unknown or ill-typed field value."""


class OptionRow(TypedDict, total=False):
    """One option: a named kind with its fields, or an "other" row (`name` None) holding a hex value."""

    name: str | None
    number: int
    fields: dict[str, Any]
    raw: str
    edited: bool


class DecodedOptions(TypedDict):
    """Option bytes decoded. `raw` (base64 of the bytes) is present only when `problem` is set."""

    layer: OptionLayer
    rows: list[OptionRow]
    padding: int
    length: int
    padded: int
    problem: str | None
    raw: NotRequired[str]


def _ints_spec(name: str, widths: list[int], variable: bool) -> dict[str, Any]:
    """The field spec of a list of ints of the given byte `widths` (one width, repeated, when variable)."""
    return {"name": name, "kind": "ints", "widths": widths, "variable": variable,
            "default": [] if variable else [0] * len(widths)}


def _int_spec(name: str, width: int, default: int) -> dict[str, Any]:
    """The field spec of an unsigned int `width` bytes wide."""
    return {"name": name, "kind": "int", "width": width, "min": 0, "max": 256 ** width - 1,
            "default": default}


def _ip_field_spec(fd: Any) -> dict[str, Any]:
    """The spec of one Scapy IP option field, by the field's class; RuntimeError for a class not handled."""
    from scapy.fields import (
        ByteField,
        FieldListField,
        IPField,
        ShortEnumField,
        ShortField,
        StrFixedLenField,
    )
    name = fd.name
    if isinstance(fd, ShortEnumField):
        values = {label: num for num, label in sorted(fd.i2s.items())}
        return {"name": name, "kind": "enum", "width": 2, "values": values, "min": 0, "max": 65535,
                "default": fd.default}
    if isinstance(fd, ByteField):
        return _int_spec(name, 1, fd.default)
    if isinstance(fd, ShortField):
        return _int_spec(name, 2, fd.default)
    if isinstance(fd, IPField):
        return {"name": name, "kind": "address", "default": fd.default}
    if isinstance(fd, FieldListField) and isinstance(fd.field, IPField):
        return {"name": name, "kind": "addresses", "default": [], "max_count": MAX_VALUE_BYTES // 4}
    if isinstance(fd, StrFixedLenField):
        size = fd.length_from(None)
        return {"name": name, "kind": "hex", "size": size, "default": fd.default.hex()}
    raise RuntimeError(f"IP option field {name} is of a class the options codec does not handle: "
                       f"{type(fd).__name__}")


def _tcp_field_spec(fmt: str | None) -> list[dict[str, Any]]:
    """The fields of a TCP option kind from its `TCPOptions` format: none, or one field `value`."""
    if fmt is None:
        return []
    if fmt == "!":
        return [_ints_spec("value", [4], True)]
    if fmt == "!p":
        return [{"name": "value", "kind": "hex", "default": ""}]
    if fmt in ("!B", "!H"):
        return [_int_spec("value", struct.calcsize(fmt), 0)]
    if fmt in ("!II", "!BH"):
        widths = [struct.calcsize("!" + c) for c in fmt[1:]]
        return [_ints_spec("value", widths, False)]
    raise RuntimeError(f"TCP option format {fmt} is not one the options codec handles")


@functools.cache
def _kinds(layer: OptionLayer) -> dict[int, dict[str, Any]]:
    """The named kinds of `layer`, by type byte: `{"name", "number", "fields": [spec, ...]}`."""
    out: dict[int, dict[str, Any]] = {}
    if layer == "IP":
        from scapy.layers import inet
        for attr in dir(inet):
            if not (attr.startswith("IPOption_") and isinstance(getattr(inet, attr), type)):
                continue
            cls: Any = getattr(inet, attr)
            opt = cls()
            number = (opt.copy_flag << 7) | (opt.optclass << 5) | opt.option
            fields = [_ip_field_spec(fd) for fd in cls.fields_desc if fd.name not in IP_ID_FIELDS]
            out[number] = {"name": attr.removeprefix("IPOption_"), "number": number, "fields": fields}
    else:
        from scapy.layers.inet import TCPOptions
        for number, (name, fmt) in TCPOptions[0].items():
            out[number] = {"name": name, "number": number, "fields": _tcp_field_spec(fmt)}
    return dict(sorted(out.items()))


def option_kinds(layer: OptionLayer) -> list[dict[str, Any]]:
    """The named kinds of `layer` in type-number order, each `{"name", "number", "fields": [...]}`; a field
    is `{"name", "kind", "default", ...}` with `kind` one of int, enum, address, addresses, ints, hex."""
    return copy.deepcopy(list(_kinds(layer).values()))


def _by_name(layer: OptionLayer, name: str) -> dict[str, Any] | None:
    """The kind of `layer` called `name`, or None."""
    return next((k for k in _kinds(layer).values() if k["name"] == name), None)


# ---- the walk -------------------------------------------------------------------------------------------

def _walk(layer: OptionLayer, data: bytes) -> tuple[list[bytes], int, str | None]:
    """Split `data` into option byte strings. Returns (options, padding, problem): the options up to and
    including the first EOL and any option after it; `padding` the zeros after the first EOL when only zeros
    follow it; `problem` text when the bytes do not parse (the options are then empty)."""
    spans: list[bytes] = []
    i = 0
    padding = 0
    seen_eol = False
    while i < len(data):
        t = data[i]
        if t in (0, 1):
            spans.append(data[i:i + 1])
            i += 1
            if t == 0 and not seen_eol:
                seen_eol = True
                tail = data[i:]
                if tail and not any(tail):
                    padding = len(tail)
                    break
            continue
        remaining = len(data) - i
        if i + 1 >= len(data):
            return [], 0, f"option {t} at byte {i} has no length byte"
        n = data[i + 1]
        if layer == "IP" and n < 2:
            return [], 0, f"option {t} at byte {i} has length {n}, below 2"
        if n > remaining:
            return [], 0, f"option {t} at byte {i} claims {n} bytes, {remaining} remain"
        n = max(n, 2)           # TCPOptionsField.m2i: a length below 2 spans the two header bytes
        spans.append(data[i:i + n])
        i += n
    return spans, padding, None


# ---- one field: decode and coerce ----------------------------------------------------------------------

def _field_width(spec: dict[str, Any]) -> int | None:
    """The fixed byte width of a field, or None when it takes the rest of the option."""
    kind = spec["kind"]
    if kind in ("int", "enum"):
        width: int = spec["width"]
        return width
    if kind == "address":
        return 4
    if kind == "ints" and not spec["variable"]:
        return int(sum(spec["widths"]))
    if kind == "hex":
        size: int | None = spec.get("size")
        return size
    return None


def _decode_field(spec: dict[str, Any], data: bytes) -> Any:
    """The value of the field `spec` held by exactly `data`, or ValueError when `data` cannot be one."""
    kind = spec["kind"]
    if kind in ("int", "enum"):
        return int.from_bytes(data, "big")
    if kind == "address":
        return str(ipaddress.IPv4Address(data))
    if kind == "hex":
        return data.hex()
    if kind == "addresses" or spec["variable"]:
        if len(data) % 4:
            raise ValueError("not a whole number of 4-byte elements")
        if kind == "addresses":
            return [str(ipaddress.IPv4Address(data[i:i + 4])) for i in range(0, len(data), 4)]
        return [int.from_bytes(data[i:i + 4], "big") for i in range(0, len(data), 4)]
    out, pos = [], 0
    for w in spec["widths"]:
        out.append(int.from_bytes(data[pos:pos + w], "big"))
        pos += w
    return out


def _decode_fields(specs: list[dict[str, Any]], body: bytes) -> dict[str, Any]:
    """The fields of `specs` read from `body` in order, the last one taking the rest when it has no fixed
    width; ValueError when `body` is not exactly those fields."""
    out: dict[str, Any] = {}
    pos = 0
    for spec in specs:
        width = _field_width(spec)
        end = len(body) if width is None else pos + width
        if end > len(body):
            raise ValueError("too short")
        out[spec["name"]] = _decode_field(spec, body[pos:end])
        pos = end
    if pos != len(body):
        raise ValueError("too long")
    return out


def _is_int(value: Any) -> bool:
    """True for a JSON int; a boolean is not one."""
    return isinstance(value, int) and not isinstance(value, bool)


def _coerce_int(value: Any, width: int, lo: int, hi: int, who: str) -> bytes:
    """`value` as an unsigned int of `width` bytes within lo..hi, else BadOption naming `who`."""
    if not _is_int(value):
        raise BadOption(f"{who} is not an integer")
    if not lo <= value <= hi:
        raise BadOption(f"{who} is {value}, outside {lo}..{hi}")
    return int(value).to_bytes(width, "big")


def _coerce_address(value: Any, who: str) -> bytes:
    """`value` as one strict dotted-quad IPv4 address in 4 bytes, else BadOption naming `who`."""
    if not isinstance(value, str):
        raise BadOption(f"{who} is not an IPv4 address")
    try:
        return ipaddress.IPv4Address(value).packed
    except ValueError:
        raise BadOption(f"{who} is not a dotted-quad IPv4 address: {value!r}") from None


def _coerce_hex(value: Any, size: int | None, who: str) -> bytes:
    """`value` as even-length hexadecimal of `size` bytes when fixed, else at most 38 bytes; BadOption."""
    if not isinstance(value, str):
        raise BadOption(f"{who} is not a hex string")
    if size is None and len(value) > 2 * MAX_VALUE_BYTES:
        raise BadOption(f"{who} is over {MAX_VALUE_BYTES} bytes")
    if len(value) % 2 or not _HEX.fullmatch(value):
        raise BadOption(f"{who} is not even-length hexadecimal")
    if size is not None and len(value) != 2 * size:
        raise BadOption(f"{who} is {len(value) // 2} bytes, not {size}")
    return bytes.fromhex(value)


def _coerce_list(value: Any, count_limit: int, who: str) -> list[Any]:
    """`value` as a list of at most `count_limit` elements, checked before any element is coerced."""
    if not isinstance(value, list):
        raise BadOption(f"{who} is not a list")
    if len(value) > count_limit:
        raise BadOption(f"{who} has {len(value)} elements, at most {count_limit}")
    return value


def _coerce_field(spec: dict[str, Any], value: Any, who: str) -> bytes:
    """The bytes of one field value coerced to its kind; BadOption naming `who` when it does not fit."""
    kind = spec["kind"]
    if kind == "int":
        return _coerce_int(value, spec["width"], spec["min"], spec["max"], who)
    if kind == "enum":
        if isinstance(value, str):
            if value not in spec["values"]:
                raise BadOption(f"{who} is {value!r}, not one of {sorted(spec['values'])}")
            value = spec["values"][value]
        return _coerce_int(value, spec["width"], spec["min"], spec["max"], who)
    if kind == "address":
        return _coerce_address(value, who)
    if kind == "hex":
        return _coerce_hex(value, spec.get("size"), who)
    if kind == "addresses":
        items = _coerce_list(value, MAX_VALUE_BYTES // 4, who)
        return b"".join(_coerce_address(a, f"{who}[{n}]") for n, a in enumerate(items))
    widths: list[int] = spec["widths"]
    if spec["variable"]:
        items = _coerce_list(value, MAX_VALUE_BYTES // widths[0], who)
        widths = widths * len(items)
    else:
        items = _coerce_list(value, len(widths), who)
        if len(items) != len(widths):
            raise BadOption(f"{who} has {len(items)} elements, not {len(widths)}")
    return b"".join(_coerce_int(v, w, 0, 256 ** w - 1, f"{who}[{n}]")
                    for n, (v, w) in enumerate(zip(items, widths, strict=True)))


def _encode_named(kind: dict[str, Any], fields: dict[str, Any], who: str) -> bytes:
    """The bytes of a named option of `kind` with `fields`: the type byte, a length byte, the fields; EOL
    and NOP are the type byte alone."""
    specs: list[dict[str, Any]] = kind["fields"]
    if not isinstance(fields, dict):
        raise BadOption(f"{who} fields are not an object")
    unknown = sorted(set(fields) - {s["name"] for s in specs})
    if unknown:
        raise BadOption(f"{who} has no field {unknown[0]}")
    body = b""
    for spec in specs:
        if spec["name"] not in fields:
            raise BadOption(f"{who} is missing field {spec['name']}")
        body += _coerce_field(spec, fields[spec["name"]], f"{who} field {spec['name']}")
    number: int = kind["number"]
    if number in (0, 1):
        return bytes([number])
    return bytes([number, len(body) + 2]) + body


def _encode_other(row: OptionRow, who: str) -> bytes:
    """The bytes of an "other" row: `[number, len(value) + 2, value]`."""
    number = row.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or not 2 <= number <= 255:
        raise BadOption(f"{who} has a number outside 2..255")
    fields = row.get("fields")
    if not isinstance(fields, dict) or set(fields) != {"value"}:
        raise BadOption(f"{who} needs exactly the field value")
    value = _coerce_hex(fields["value"], None, f"{who} field value")
    return bytes([number, len(value) + 2]) + value


def _row_bytes(layer: OptionLayer, row: OptionRow) -> bytes:
    """The bytes of one row: its `raw` when it has one and is not edited, else built from its `fields`."""
    raw = row.get("raw")
    if raw is not None and not row.get("edited"):
        try:
            return base64.b64decode(raw, validate=True)
        except (binascii.Error, TypeError, ValueError):
            raise BadOption("an option's raw is not base64") from None
    name = row.get("name")
    if name is None:
        return _encode_other(row, f"option {row.get('number')}")
    found = _by_name(layer, name) if isinstance(name, str) else None
    if found is None:
        raise BadOption(f"{name!r} is not a known {layer} option")
    return _encode_named(found, row.get("fields", {}), name)


# ---- decode, encode, text ----------------------------------------------------------------------------------

def _decode_row(layer: OptionLayer, span: bytes) -> OptionRow:
    """The row for the option bytes `span`: named when its kind encodes the decoded fields back to exactly
    `span`, else an "other" row with the value as hex."""
    t = span[0]
    raw = base64.b64encode(span).decode("ascii")
    if t in (0, 1):
        kind = _kinds(layer)[t]
        return {"name": kind["name"], "number": t, "fields": {}, "raw": raw}
    named = _kinds(layer).get(t)
    if named is not None:
        kind = named
        try:
            fields = _decode_fields(kind["fields"], span[2:])
            if _encode_named(kind, fields, kind["name"]) == span:
                return {"name": kind["name"], "number": t, "fields": fields, "raw": raw}
        except ValueError:      # BadOption is a ValueError: a value the table cannot hold
            pass
    return {"name": None, "number": t, "fields": {"value": span[2:].hex()}, "raw": raw}


def decode(layer: OptionLayer, raw: bytes) -> DecodedOptions:
    """The option bytes `raw` as rows. Bytes that do not parse give no rows and a `problem` (and `raw` in
    base64); `padded` is 0 here, the worker sets it."""
    spans, padding, problem = _walk(layer, raw)
    result: DecodedOptions = {"layer": layer, "rows": [_decode_row(layer, s) for s in spans],
                              "padding": padding, "length": len(raw) - padding, "padded": 0,
                              "problem": problem}
    if problem is not None:
        result["raw"] = base64.b64encode(raw).decode("ascii")
    return result


def encode(layer: OptionLayer, rows: list[OptionRow], padding: int, length: int) -> bytes:
    """The bytes of `rows` then the padding: `padding` zeros kept as given when the rows' byte length equals
    `length`, else zeros to the next 4-byte boundary. OptionsTooLong over 40 bytes, as soon as the rows
    pass it; BadOption for a row that does not fit its kind."""
    out = b""
    for row in rows:
        out += _row_bytes(layer, row)
        if len(out) > MAX_OPTION_BYTES:
            raise OptionsTooLong(f"options are {len(out)} bytes; the header holds {MAX_OPTION_BYTES} at most")
    pad = padding if len(out) == length else -len(out) % 4
    if len(out) + pad > MAX_OPTION_BYTES:
        raise OptionsTooLong(
            f"options are {len(out) + pad} bytes; the header holds {MAX_OPTION_BYTES} at most")
    return out + bytes(pad)


def _repr(spec: dict[str, Any], value: Any) -> str:
    """A field value as the options text shows it: an enum by its label when it has one, an address list as
    `16.0.0.1, 48.0.0.1`, an int list as `(1, 2)`."""
    if spec["kind"] == "enum":
        return next((label for label, num in spec["values"].items() if num == value), str(value))
    if spec["kind"] == "ints":
        return "(" + ", ".join(str(v) for v in value) + ")"
    if spec["kind"] == "addresses":
        return ", ".join(str(v) for v in value)
    return str(value)


def _row_text(layer: OptionLayer, row: OptionRow) -> str:
    """One row as text: `Router Alert (alert=router_shall_examine_packet)`, `MSS (value=1460)`, `NOP`."""
    name = row.get("name")
    if name is None:
        return f"option {row['number']} (value={row['fields']['value']})"
    kind = _by_name(layer, name)
    shown = [f"{s['name']}={_repr(s, row['fields'][s['name']])}" for s in kind["fields"]] if kind else []
    label = name.replace("_", " ") if layer == "IP" else name
    return f"{label} ({', '.join(shown)})" if shown else label


def options_text(layer: OptionLayer, decoded: DecodedOptions) -> str:
    """The decoded options as one line: `NOP, Router Alert (alert=router_shall_examine_packet), EOL, padding 2
    bytes`; `[]` when there is nothing; `unparsed: <hex> (<problem>)` when the bytes did not parse."""
    if decoded["problem"] is not None:
        shown = base64.b64decode(decoded.get("raw", "")).hex()
        text = f"unparsed: {shown} ({decoded['problem']})"
        if decoded["padded"]:
            text += f", padded to {decoded['length'] + decoded['padding'] + decoded['padded']} bytes"
        return text
    parts = [_row_text(layer, row) for row in decoded["rows"]]
    pad = decoded["padding"]
    if pad:
        parts.append(f"padding {pad} byte{'' if pad == 1 else 's'}")
    if not parts:
        return "[]"
    text = ", ".join(parts)
    if decoded["padded"]:
        total = decoded["length"] + pad + decoded["padded"]
        text += f", padded to {total} bytes"
    return text
