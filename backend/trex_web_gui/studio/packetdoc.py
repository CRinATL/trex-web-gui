"""The packet editor's document (`.trp`, P4b plan Task 2): parsing, id checks, and the literal-only rule.

Pure: nothing here imports `trex` or `scapy`, and nothing evaluates a value. A document is the file the legacy
editor wrote: `packet` (layers with field values), `fePrarameters` (global Field Engine parameters; the
misspelling is the file format's) and `feInstructions`. `feParameters` is accepted as an alias.

A document derived from packet bytes carries no evaluable code (plan D13): `literal_expr` is the one gate
through which an object value may become an EXPRESSION, and it admits only Python literals."""
import ast
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from trex_web_gui.trexapi.packet import Definitions

FILE_TYPE = "trex-packet-editor"
VERSION = "1.0.0"
VTYPES = ("EXPRESSION", "BYTES", "RANDOM", "UNDEFINED", "MACHINE", "OBJECT")
BYTES_GENERATORS = ("random_bytes", "random_ascii", "template", "template_code")
BYTES_SIZE_MAX = 1024 * 1024         # verify_payload_size, scapy_service.py:406-409
VALUE_DEPTH_MAX = 8                  # list nesting a field value may have
LITERAL_DEPTH_MAX = 32               # nesting `is_literal` looks into
TOP_KEYS = ("fileType", "version", "metadata", "packet", "fePrarameters", "feParameters", "feInstructions")
LITERAL_TYPES = (int, str, bytes, type(None))


class DocField(TypedDict):
    id: str
    value: Any


class DocLayer(TypedDict):
    id: str
    fields: list[DocField]


class FeInstruction(TypedDict):
    id: str
    parameters: dict[str, Any]


class DocMetadata(TypedDict):
    caption: str


class PacketDoc(TypedDict):
    fileType: str
    version: str
    metadata: DocMetadata
    packet: list[DocLayer]
    fePrarameters: dict[str, Any]
    feInstructions: list[FeInstruction]


class DocError(ValueError):
    """A document the editor refuses. `where` names the offending key, for example `packet[2].fields[0]`;
    `message` is the reason alone; `label` is the position a person reads (`layer 1`, `instruction 0`) when
    the check knows one, else None."""

    def __init__(self, where: str, message: str, label: str | None = None) -> None:
        """Keep `where`, `message` and `label`; the text is `"<where>: <message>"`."""
        super().__init__(f"{where}: {message}")
        self.where = where
        self.message = message
        self.label = label


class NotRepresentable(ValueError):
    """A field value that cannot be a document value without evaluable code (plan D13)."""


def empty_doc() -> PacketDoc:
    """A new document: one Ether layer and the default cache size."""
    return {"fileType": FILE_TYPE, "version": VERSION, "metadata": {"caption": ""},
            "packet": [{"id": "Ether", "fields": []}], "fePrarameters": {"cache_size": "1000"},
            "feInstructions": []}


def is_literal(value: object) -> bool:
    """True when `value` is built only from list, tuple, int, str, bytes and None (exact types: a bool or an
    int subclass is not), and `ast.literal_eval(repr(value))` equals it."""
    return literal_expr(value) is not None


def literal_expr(value: object) -> str | None:
    """`repr(value)` when `value` is a Python literal (see `is_literal`) that `ast.literal_eval` reads back
    as an equal value; else None. The expression is safe to hand to `eval` because it holds no name or
    call (plan D13)."""
    if not _plain(value, 0):
        return None
    text = repr(value)
    try:
        back = ast.literal_eval(text)
    except (ValueError, SyntaxError, RecursionError):
        return None
    return text if back == value and _same_shape(back, value) else None


def _plain(value: object, depth: int) -> bool:
    """True when every node of `value` has an exact literal type, to LITERAL_DEPTH_MAX levels."""
    if depth > LITERAL_DEPTH_MAX:
        return False
    t = type(value)
    if t is list or t is tuple:
        return all(_plain(v, depth + 1) for v in value)                      # type: ignore[attr-defined]
    return t in LITERAL_TYPES


def _same_shape(a: object, b: object) -> bool:
    """True when `a` and `b` have the same types at every node (list vs tuple, int vs str)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return len(a) == len(b) and all(_same_shape(x, y) for x, y in zip(a, b, strict=True))
    return True


def doc_value(v: object) -> Any:
    """The document value for a field value `v` read from a packet: an int (also a flags value), a str, bytes
    as `{vtype: "BYTES", base64}`, None as null, or an object as `{vtype: "EXPRESSION", expr}` when it is
    a literal (D13). A str stays a str, never an expression. NotRepresentable for any other object."""
    import base64
    if v is None:
        return None
    # Scapy's FlagValue is an `object`, not an int subclass; recognised by name to keep this module pure.
    if isinstance(v, bool | int) or any(c.__name__ == "FlagValue" for c in type(v).__mro__):
        return int(v)                                           # type: ignore[call-overload]
    if isinstance(v, str):
        return str.__str__(v)
    if isinstance(v, bytes | bytearray):
        return {"vtype": "BYTES", "base64": base64.b64encode(bytes(v)).decode("ascii")}
    if (expr := literal_expr(v)) is not None:
        return {"vtype": "EXPRESSION", "expr": expr}
    raise NotRepresentable(f"{type(v).__name__} is not a literal")


def is_vtype(v: object) -> bool:
    """True when `v` is an object with a `vtype` key (its checks are `_check_vtype`'s)."""
    return isinstance(v, dict) and "vtype" in v


def _is_number(v: object) -> bool:
    """True for an int or float that is not a bool."""
    return isinstance(v, int | float) and not isinstance(v, bool)


def _check_size(where: str, v: object) -> None:
    """A BYTES `size` or `total_size`: an int of 1-1048576."""
    if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= BYTES_SIZE_MAX:
        raise DocError(where, f"must be a whole number of 1-{BYTES_SIZE_MAX}")


def _check_vtype(where: str, v: dict[str, Any]) -> None:
    """Check one vtype object: a known `vtype` and the keys that vtype needs."""
    vt = v.get("vtype")
    if vt not in VTYPES:
        raise DocError(where, f"unknown vtype {vt!r}; expected one of {', '.join(VTYPES)}")
    if vt == "EXPRESSION" and not isinstance(v.get("expr"), str):
        raise DocError(where, "an EXPRESSION needs a string expr")
    if vt == "MACHINE" and not isinstance(v.get("base64"), str):
        raise DocError(where, "a MACHINE value needs a base64 string")
    if vt == "OBJECT" and "value" not in v:
        raise DocError(where, "an OBJECT value needs a value")
    if vt == "BYTES":
        gen = v.get("generate")
        if gen is None:
            if not isinstance(v.get("base64"), str):
                raise DocError(where, "a BYTES value needs a base64 string or a generate")
        elif gen not in BYTES_GENERATORS:
            raise DocError(where, f"unknown generate {gen!r}; expected one of {', '.join(BYTES_GENERATORS)}")
        for key in ("size", "total_size"):
            if key in v:
                _check_size(f"{where}.{key}", v[key])


def _check_value(where: str, v: object, depth: int = 0) -> None:
    """A field value: str, int, float, bool, null, a vtype object, or a list of those."""
    if v is None or isinstance(v, str | bool) or _is_number(v):
        return
    if isinstance(v, dict):
        _check_vtype(where, v)
        return
    if isinstance(v, list):
        if depth >= VALUE_DEPTH_MAX:
            raise DocError(where, "lists nest too deeply")
        for i, item in enumerate(v):
            _check_value(f"{where}[{i}]", item, depth + 1)
        return
    raise DocError(where, f"a {type(v).__name__} cannot be a field value")


def _check_fe_value(where: str, v: object) -> None:
    """A Field Engine parameter value: a str or a vtype object (the service calls `val.lower()` on others)."""
    if isinstance(v, str):
        return
    if isinstance(v, dict):
        _check_vtype(where, v)
        return
    raise DocError(where, f"must be a string or a vtype object, not {type(v).__name__}")


def _str_key_dict(where: str, v: object) -> dict[str, Any]:
    """`v` as a dict with str keys, else a DocError."""
    if not isinstance(v, dict) or not all(isinstance(k, str) for k in v):
        raise DocError(where, "must be an object")
    return v


def _parse_layers(raw: object) -> list[DocLayer]:
    """The `packet` list: layers `{id, fields?}` with fields `{id, value}`."""
    if not isinstance(raw, list) or not raw:
        raise DocError("packet", "must be a list of at least one layer")
    layers: list[DocLayer] = []
    for i, layer in enumerate(raw):
        where = f"packet[{i}]"
        d = _str_key_dict(where, layer)
        if extra := sorted(set(d) - {"id", "fields"}):
            raise DocError(where, f"unknown key {extra[0]}")
        if not isinstance(d.get("id"), str) or not d["id"]:
            raise DocError(f"{where}.id", "must be a protocol name")
        fields: list[DocField] = []
        raw_fields = d.get("fields", [])
        if not isinstance(raw_fields, list):
            raise DocError(f"{where}.fields", "must be a list")
        for j, f in enumerate(raw_fields):
            fw = f"{where}.fields[{j}]"
            fd = _str_key_dict(fw, f)
            if extra := sorted(set(fd) - {"id", "value"}):
                raise DocError(fw, f"unknown key {extra[0]}")
            if not isinstance(fd.get("id"), str) or not fd["id"]:
                raise DocError(f"{fw}.id", "must be a field name")
            if "value" not in fd:
                raise DocError(fw, "needs a value")
            _check_value(f"{fw}.value", fd["value"])
            fields.append({"id": fd["id"], "value": fd["value"]})
        layers.append({"id": d["id"], "fields": fields})
    return layers


def _parse_fe_instructions(raw: object) -> list[FeInstruction]:
    """The `feInstructions` list: `{id, parameters}` with str or vtype parameter values."""
    if not isinstance(raw, list):
        raise DocError("feInstructions", "must be a list")
    out: list[FeInstruction] = []
    for i, ins in enumerate(raw):
        where = f"feInstructions[{i}]"
        d = _str_key_dict(where, ins)
        if extra := sorted(set(d) - {"id", "parameters"}):
            raise DocError(where, f"unknown key {extra[0]}")
        if not isinstance(d.get("id"), str) or not d["id"]:
            raise DocError(f"{where}.id", "must be an instruction name")
        params = _str_key_dict(f"{where}.parameters", d.get("parameters", {}))
        for k, v in params.items():
            _check_fe_value(f"{where}.parameters.{k}", v)
        out.append({"id": d["id"], "parameters": dict(params)})
    return out


def parse_doc(raw: object) -> PacketDoc:
    """The document `raw` (parsed JSON) as a PacketDoc, or DocError. `feParameters` is accepted as an alias
    of `fePrarameters` and returned under the file format's spelling; missing Field Engine keys become
    `{}` and `[]`; an unknown top-level key is refused."""
    d = _str_key_dict("document", raw)
    if extra := sorted(set(d) - set(TOP_KEYS)):
        raise DocError(extra[0], "unknown key")
    if d.get("fileType", FILE_TYPE) != FILE_TYPE:
        raise DocError("fileType", f"must be {FILE_TYPE!r}")
    version = d.get("version", VERSION)
    if not isinstance(version, str):
        raise DocError("version", "must be a string")
    meta = _str_key_dict("metadata", d.get("metadata", {}))
    if extra := sorted(set(meta) - {"caption"}):
        raise DocError(f"metadata.{extra[0]}", "unknown key")
    caption = meta.get("caption", "")
    if not isinstance(caption, str):
        raise DocError("metadata.caption", "must be a string")
    if "packet" not in d:
        raise DocError("packet", "is required")
    if "fePrarameters" in d and "feParameters" in d:
        raise DocError("feParameters", "give fePrarameters or feParameters, not both")
    key = "fePrarameters" if "fePrarameters" in d else "feParameters"
    params = _str_key_dict(key, d.get(key, {}))
    for k, v in params.items():
        _check_fe_value(f"{key}.{k}", v)
    return {"fileType": FILE_TYPE, "version": version, "metadata": {"caption": caption},
            "packet": _parse_layers(d["packet"]), "fePrarameters": dict(params),
            "feInstructions": _parse_fe_instructions(d.get("feInstructions", []))}


def check_ids(doc: PacketDoc, defs: "Definitions") -> None:
    """DocError unless every id in `doc` is one the definitions know: layer ids against `layerIds` (every
    Packet subclass), instruction ids against `feInstructions`, parameter ids against
    `feInstructionParameters`, global parameter ids against `feParameters`. The message names the
    position, for example `layer 1: unknown protocol Foo`."""
    layers = set(defs["layerIds"])
    for i, layer in enumerate(doc["packet"]):
        if layer["id"] not in layers:
            raise DocError(f"packet[{i}].id", f"layer {i}: unknown protocol {layer['id']}", f"layer {i}")
    instructions = {m["id"] for m in defs["feInstructions"]}
    params = {m["id"] for m in defs["feInstructionParameters"]}
    for i, ins in enumerate(doc["feInstructions"]):
        if ins["id"] not in instructions:
            raise DocError(f"feInstructions[{i}].id", f"instruction {i}: unknown instruction {ins['id']}",
                           f"instruction {i}")
        for k in ins["parameters"]:
            if k not in params:
                raise DocError(f"feInstructions[{i}].parameters.{k}",
                               f"instruction {i} {ins['id']}: unknown parameter {k}", f"instruction {i}")
    globals_ = {m["id"] for m in defs["feParameters"]}
    for k in doc["fePrarameters"]:
        if k not in globals_:
            raise DocError(f"fePrarameters.{k}", f"unknown global parameter {k}")


def model_of(doc: PacketDoc) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """The service's two models: the packet layers, and the Field Engine model
    `{"instructions", "global_parameters"}`, or None when the document has no instructions."""
    packet: list[dict[str, Any]] = [dict(layer) for layer in doc["packet"]]
    if not doc["feInstructions"]:
        return packet, None
    return packet, {"instructions": doc["feInstructions"], "global_parameters": doc["fePrarameters"]}
