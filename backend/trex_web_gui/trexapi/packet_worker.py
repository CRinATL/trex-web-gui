"""Subprocess entry point for the packet editor (P4b plan D1), and for the capture viewer's layer tree: one
persistent worker holds the only `Scapy_service` either uses and answers one JSON request line at a time.
Run by packet.py as: python -I -B -c <bootstrap: an address-space limit, then this module as __main__>.
Never imported by the backend process: a packet field's EXPRESSION value is arbitrary Python that the
service `eval`s (`scapy_service.py:563-580`), so every build runs here, under the limit.

Protocol: the first line written is `{"ready": true, "pdeathsig": <bool>}`. Then, for each request line
`{id, op, args}` read from stdin, one line echoing `id` is written: `{"id", "ok": true, ...}` or
`{"id", "ok": false, "error": str}`. A MemoryError writes a line built before the work began. The worker
exits 0 on stdin EOF. The results go to a DUP of the original stdout fd, taken before anything runs; fds 1
and 2 are then pointed at /dev/null, so a print or an accidental write cannot reach the result pipe. A
deliberate one can (an expression may `os.write` to the dup'd fd); the id echo is the guard: a forged or extra
line fails the parent's id check, which kills the worker and raises PacketError(restarted=True)."""
import base64
import ctypes
import hashlib
import inspect
import json
import os
import signal
import struct
import sys
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from trex_web_gui.studio.packetdoc import NotRepresentable, doc_value, empty_doc, literal_expr

if TYPE_CHECKING:
    from trex_web_gui.trexapi.packet_options import OptionLayer

PR_SET_PDEATHSIG = 1
PARENT_POLL_S = 1.0        # the getppid watchdog's period (platforms without prctl)
ETHER_LEN = 14
# A list row's summary dissects only a record's first bytes: the headers it names sit there, and dissection
# cost grows with length (a zero-filled IPv4 record parses as IPv6 hop-by-hop options, one Pad1 per byte).
SUMMARY_BYTES = 2048
# The editor and the capture viewer's tree (the name predates the viewer) dissect at most this many bytes of a
# record into layers; the rest is one raw layer. Dissection of a zero-filled 70 000-byte IPv4 record took
# 12.9 s (the same Pad1 chain), against the 5 s call timeout.
# Equal to studio.streams.PKT_MAX (a literal: streams is not imported here), so no packet Studio can build is
# ever capped.
EDITOR_DECODE_BYTES = 9238
TRUNCATED_WARNING = ("the record is {n} bytes; only the first " + str(EDITOR_DECODE_BYTES) +
                     " are decoded into layers, the rest is one raw layer")
FALLBACK_WARNING = ("the byte edit re-derived the packet from its bytes: generated and expression values "
                    "became literal values")
NOT_FIELDWISE_WARNING = "the packet could not be represented field by field"


def set_parent_death_signal() -> bool:
    """Ask the kernel to SIGKILL this process when its parent dies (Linux `prctl(PR_SET_PDEATHSIG)`).
    True when prctl returned 0; False on any other platform or failure."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return bool(libc.prctl(PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0) == 0)
    except (OSError, AttributeError):
        return False


def parent_gone() -> bool:
    """True when the parent named in TREX_WEB_GUI_PARENT_PID is no longer this process's parent."""
    want = os.environ.get("TREX_WEB_GUI_PARENT_PID", "")
    return not want.isdigit() or os.getppid() != int(want)


def _watch_parent() -> None:
    """Daemon thread body: exit the whole process as soon as the parent is gone."""
    while True:
        time.sleep(PARENT_POLL_S)
        if parent_gone():
            os._exit(1)


def _caused_by_memory(e: BaseException) -> bool:
    """True when `e` or anything in its cause/context chain is a MemoryError (the service re-raises what an
    expression raised, sometimes wrapped)."""
    seen = 0
    while e is not None and seen < 20:
        if isinstance(e, MemoryError):
            return True
        e = e.__cause__ or e.__context__            # type: ignore[assignment]
        seen += 1
    return False


class Engine:
    """The service and its handler, constructed once per worker; `ops` maps an op name to its function."""

    def __init__(self) -> None:
        """Load the vendored client and construct `Scapy_service` (the slow step, about 0.2 s)."""
        from trex_web_gui.trexapi.loader import load
        load()
        from scapy.packet import Packet
        from trex.scapy_server.scapy_service import Scapy_service
        self.packet_class = Packet
        self.svc = Scapy_service()
        names = ("_fully_define", "_packet_model_to_scapy_packet", "_pkt_data", "_pkt_to_field_tree",
                 "_field_value_from_def")
        for name in names:
            if not callable(getattr(self.svc, name, None)):
                raise RuntimeError(f"the vendored Scapy_service has no method {name}; the packet editor's "
                                   "decode cap and the capture viewer's tree rely on it")
        self.h = self.svc.get_version_handler("1", "02")
        # Every build describes the packet by dissecting its bytes again (`_fully_define`); bound that to the
        # decode cap. The binary is still the whole packet; only the field tree stops at the cap.
        self.svc._fully_define = self._bounded_define
        # Layer offsets come from the bytes (look-7, `_field_tree`); keep the service's own method to call.
        self._service_field_tree = self.svc._pkt_to_field_tree
        self.svc._pkt_to_field_tree = self._field_tree
        # An IP or TCP `options` field of MACHINE bytes is built from exactly those bytes (`_field_value`).
        self._service_field_value = self.svc._field_value_from_def
        self.svc._field_value_from_def = self._field_value
        self._definitions: dict[str, Any] | None = None
        self.ops: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "definitions": self.definitions, "build": self.build, "payload_classes": self.payload_classes,
            "param_values": self.param_values, "doc_from_binary": self.doc_from_binary,
            "reconstruct_edit": self.reconstruct_edit, "decompile": self.decompile,
            "templates": self.templates, "template": self.template, "pcap_list": self.pcap_list,
            "pcap_packet": self.pcap_packet, "to_stream": self.to_stream, "to_code": self.to_code,
            "tree": self.tree}

    def definitions(self, args: dict[str, Any]) -> dict[str, Any]:
        """`get_definitions(h, None)` plus `etag` (the first 16 hex digits of the sha256 of the canonical
        definitions JSON, plan D6) and `layerIds` (the sorted name of every Packet subclass); computed
        once."""
        if self._definitions is None:
            d = self.svc.get_definitions(self.h, None)
            canon = json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)
            etag = hashlib.sha256(canon.encode()).hexdigest()[:16]
            ids = sorted({c.__name__ for c in self.svc._all_subclasses(self.packet_class)})
            self._definitions = {**d, "etag": etag, "layerIds": ids}
        return {"ok": True, **self._definitions}

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        """Build the packet model `args["packet"]` and, when `args["fe"]` has instructions, compile them.
        `{ok, binary, data, field_engine, error}`: an exception from the packet half becomes `error`
        (`"<Type>: <message>"`) with binary null and data []; one from the field-engine half becomes
        `field_engine.error`, and the packet is rebuilt without the FE to keep `binary`."""
        packet, fe = args["packet"], args.get("fe")
        if not fe or not fe.get("instructions"):
            return self._packet_only(packet, None)
        try:
            res = self.svc.build_pkt_ex(self.h, packet, {"field_engine": fe})
        except Exception as e:
            if _caused_by_memory(e):
                raise
            # Which half failed? One build_pkt tells; its result is kept, so nothing is built again.
            return self._packet_only(packet, {"instructions": {}, "error": _describe(e)})
        engine = res["field_engine"]
        instructions = engine["instructions"]
        return {"ok": True, "binary": res["binary"], "data": res["data"], "error": None,
                "field_engine": {"instructions": instructions if isinstance(instructions, dict) else {},
                                 "error": engine["error"]}}

    def tree(self, args: dict[str, Any]) -> dict[str, Any]:
        """`{data, binary}` for the captured frame `args["binary"]` (base64), the capture viewer's layer tree:
        the service's `reconstruct_pkt` with an empty model. A frame over EDITOR_DECODE_BYTES is dissected
        only that far and its rest attached as one Raw layer, as `_dissect` does; `binary` is the whole frame.
        Here rather than in the backend because dissection cost has no bound of its own (a zero-filled IPv4
        frame's memory grows faster than its length; an IPv6 hop-by-hop chain takes seconds), and this
        process has the call timeout and the address-space limit."""
        raw = base64.b64decode(args["binary"])
        if len(raw) <= EDITOR_DECODE_BYTES:
            return {"ok": True, **self.svc.reconstruct_pkt(None, args["binary"], [])}
        from scapy.layers.l2 import Ether
        from scapy.packet import Padding, Raw
        pkt = Ether(raw[:EDITOR_DECODE_BYTES])
        # Scapy builds a Padding's bytes after the whole packet, so a tail nested under one would come out
        # before them: from the first Padding on, the frame is one Raw tail instead.
        pad = next((x for x in _chain(pkt) if isinstance(x, Padding)), None)
        if pad is not None and pad.underlayer is not None:
            pad.underlayer.remove_payload()
        _chain(pkt)[-1].add_payload(Raw(raw[len(bytes(pkt)):]))
        if bytes(pkt) != raw:            # any other layout that does not rebuild in order: Ether plus Raw
            pkt = Ether(raw[:ETHER_LEN]) / Raw(raw[ETHER_LEN:])
        return {"ok": True, **self.svc._pkt_data(pkt)}

    def payload_classes(self, args: dict[str, Any]) -> dict[str, Any]:
        """`{classes}`: the protocols the last layer of `args["packet"]` may carry."""
        return {"ok": True, "classes": self.svc.get_payload_classes(self.h, args["packet"])}

    def param_values(self, args: dict[str, Any]) -> dict[str, Any]:
        """`{map}`: the values `args["param_id"]` may take for `args["packet"]` and the instructions of
        `args["fe"]` (the service's `load_instruction_parameter_values`)."""
        fe = args.get("fe") or {"instructions": [], "global_parameters": {}}
        res = self.svc.load_instruction_parameter_values(
            self.h, args["packet"], {"field_engine": fe}, args["param_id"])
        return {"ok": True, "map": res["map"]}

    def doc_from_binary(self, args: dict[str, Any]) -> dict[str, Any]:
        """`{doc, warnings}` for the packet bytes `args["binary"]` (base64) and, when `args["vm"]` has
        instructions, the Field Engine decompiled from it. The document holds literals only (plan D13)."""
        raw = base64.b64decode(args["binary"])
        layers, warnings = self._layers_of(raw)
        doc: dict[str, Any] = {**empty_doc(), "packet": layers}
        vm = args.get("vm")
        if vm and vm.get("instructions"):
            dec = self._decompile(args["binary"], vm)
            doc["feInstructions"] = dec["instructions"]
            doc["fePrarameters"] = dec["global_parameters"]
            warnings += dec["warnings"]
        return {"ok": True, "doc": doc, "warnings": warnings}

    def _layers_of(self, raw: bytes) -> tuple[list[dict[str, Any]], list[str]]:
        """The document layers for `raw`, and the warnings. Dissect with `Ether`, hide default values, delete
        each auto-computed field (class default None) that the bytes do not need; a value that is not a
        literal turns its layer and every later one into one Raw layer of those bytes (D13); a document
        that does not rebuild to `raw` becomes Ether plus Raw. Only the first EDITOR_DECODE_BYTES are
        dissected: a longer record's remainder is one Raw layer after them (and TRUNCATED_WARNING), attached
        undissected so the derived fields (lengths, checksums) are still judged over the whole record."""
        if len(raw) < ETHER_LEN:
            return [_raw_layer(raw)], [NOT_FIELDWISE_WARNING]
        capped = len(raw) > EDITOR_DECODE_BYTES
        try:
            pkt, layers, lengths = _dissect(raw, capped)
        except Exception as e:
            if not capped or _caused_by_memory(e):
                raise
            # The whole record cannot be built as one packet (a length field overflows): Ether plus Raw.
            return self._ether_raw(raw), [TRUNCATED_WARNING.format(n=len(raw)), NOT_FIELDWISE_WARNING]
        out: list[dict[str, Any]] = []
        warnings: list[str] = [TRUNCATED_WARNING.format(n=len(raw))] if capped else []
        for i, layer in enumerate(layers):
            fields: list[dict[str, Any]] = []
            for desc in layer.fields_desc:
                if desc.name not in layer.fields:
                    continue
                kind = _option_layer(layer) if desc.name == "options" else None
                if kind is not None:
                    mine = raw[len(raw) - min(lengths[i], len(raw)):]
                    fields.append({"id": "options", "value": _machine(option_bytes(kind, mine))})
                    continue
                try:
                    fields.append({"id": desc.name, "value": doc_value(layer.fields[desc.name])})
                except NotRepresentable:
                    out.append(_raw_layer(raw[len(raw) - min(lengths[i], len(raw)):]))
                    warnings.append(f"layer {i} {type(layer).__name__}: {desc.name} cannot be edited as a "
                                    "field here; from this layer on the packet is raw bytes")
                    break
            else:
                out.append({"id": type(layer).__name__, "fields": fields})
                continue
            break
        if self._rebuilds(out, raw):
            return out, warnings
        return self._ether_raw(raw), [*warnings, NOT_FIELDWISE_WARNING]

    @staticmethod
    def _ether_raw(raw: bytes) -> list[dict[str, Any]]:
        """The Ether header of `raw` (at least ETHER_LEN bytes) as fields, then the rest as one Raw layer."""
        from scapy.layers.l2 import Ether
        head = Ether(raw[:ETHER_LEN])
        ether = {"id": "Ether", "fields": [
            {"id": n, "value": doc_value(head.getfieldval(n))} for n in ("dst", "src", "type")]}
        return [ether, _raw_layer(raw[ETHER_LEN:])]

    @staticmethod
    def _bounded_define(pkt: Any) -> Any:
        """The service's `_fully_define` (the packet re-dissected from its own bytes, offsets initialised),
        reading at most EDITOR_DECODE_BYTES: a shorter packet is dissected exactly as before. A longer one's
        remainder is attached as one undissected Raw layer, as `_dissect` does, so the field tree still finds
        the raw tail."""
        data = bytes(pkt)
        full = type(pkt)(data[:EDITOR_DECODE_BYTES])
        if len(data) > EDITOR_DECODE_BYTES:
            from scapy.packet import Raw
            _chain(full)[-1].add_payload(Raw(data[EDITOR_DECODE_BYTES:]))
        full.build()
        return full

    def _field_value(self, scapy_pkt: Any, layer: Any, field_id: str, val: Any) -> Any:
        """The service's `_field_value_from_def`, except for the `options` of an IP- or TCP-family layer given
        as MACHINE: its base64 must be strict, it holds 40 bytes at most (`OptionsTooLong`, so a document or a
        `.trp` cannot bypass the limit), and it is zero-padded to 4 bytes here, before Scapy builds it, so a
        Field Engine offset past an IP layer is right (the fork records a payload's offset before
        `IP.post_build` pads). IP gets the bytes as one Raw item of its list, TCP gets them as the value; both
        carry the document's length (`OptionBytes`) for `_retext`."""
        kind = _option_layer(layer) if field_id == "options" else None
        if kind is None or not isinstance(val, dict) or val.get("vtype") != "MACHINE":
            return self._service_field_value(scapy_pkt, layer, field_id, val)
        from scapy.config import conf

        from trex_web_gui.trexapi.packet_options import MAX_OPTION_BYTES, OptionsTooLong
        text = val.get("base64")
        try:
            if not isinstance(text, str):
                raise ValueError(text)
            given = base64.b64decode(text, validate=True)
        except ValueError:
            raise ValueError("the options value is not valid base64") from None
        if len(given) > MAX_OPTION_BYTES:
            raise OptionsTooLong(
                f"options are {len(given)} bytes; the header holds {MAX_OPTION_BYTES} at most")
        value = OptionBytes(given + bytes(-len(given) % 4))
        value.document_length = len(given)
        return [conf.raw_layer(load=value)] if kind == "IP" else value

    def _field_tree(self, pkt: Any) -> list[dict[str, Any]]:
        """The service's field tree of `pkt`, with each layer's `offset` taken from the packet's own bytes.

        The fork records a payload's offset before `IP.post_build` pads the options
        (`_vendor/external_libs/scapy-2.4.3/scapy/packet.py:661-666`), and the service reads that model
        offset (`_vendor/trex/scapy_server/scapy_service.py:705`), so with options that need padding every
        later layer was early by the pad (DEFERRED look-7). The re-dissected bytes have the right offsets.
        The model and the bytes are walked by depth, as the service's `valid_structure` does: a layer takes
        its byte offset up to and including the first one whose class differs (it still starts where the
        previous layer's payload does); deeper layers, and those past the end of the bytes, take the
        service's offset moved by the last correction. Field offsets are relative to their layer, so they
        need nothing. Costs one more dissection per tree, within the decode cap (`_bounded_define`)."""
        result: list[dict[str, Any]] = self._service_field_tree(pkt)
        model, data = _chain(pkt), _chain(self._bounded_define(pkt))
        delta, done = 0, 0
        for layer, m, b in zip(result, model, data, strict=False):
            offset = getattr(b, "_offset", None)
            if offset is not None and isinstance(layer.get("offset"), int):
                delta = offset - layer["offset"]
                layer["offset"] = offset
            done += 1
            if not (isinstance(m, type(b)) or isinstance(b, type(m))):
                break
        # The layers the walk did not reach are early by the same pad: move them by the last correction, so
        # the offsets stay in order (the frontend ends each layer where the next one starts).
        for layer in result[done:]:
            if isinstance(layer.get("offset"), int):
                layer["offset"] += delta
        _retext(result, model, data[:done])
        return result

    def _rebuilds(self, layers: list[dict[str, Any]], raw: bytes) -> bool:
        """True when the service builds `layers` into exactly `raw`. For a record over the cap only the bytes
        are compared: `build_pkt` also dissects the built bytes again to describe every field, which for a
        long zero-filled record is the cost the cap exists to avoid."""
        try:
            if len(raw) > EDITOR_DECODE_BYTES:
                return bool(bytes(self.svc._packet_model_to_scapy_packet(layers)) == raw)
            res = self.svc.build_pkt(self.h, layers)
        except Exception as e:
            if _caused_by_memory(e):
                raise
            return False
        return bool(base64.b64decode(res["binary"]) == raw)

    def reconstruct_edit(self, args: dict[str, Any]) -> dict[str, Any]:
        """`{doc, warnings}` after the byte edit that produced `args["binary"]` (D12). With `args["edited"]`
        `{layer, field}`, the bytes are reconstructed on the document's packet without that field
        (`_reconstructed`), and only that field takes its value from the bytes. Otherwise, or when the bytes
        dissect to a different layer stack, or the value is not a literal, the whole packet is re-derived from
        the bytes (the Field Engine is kept) with a warning."""
        doc, edited = args["doc"], args.get("edited")
        if edited is not None:
            try:
                return {"ok": True, "doc": {**doc, "packet": self._reconstruct(doc["packet"], args["binary"],
                                                                                edited)}, "warnings": []}
            except (_Fallback, NotRepresentable):
                pass
            except Exception as e:
                if _caused_by_memory(e):
                    raise
        layers, warnings = self._layers_of(base64.b64decode(args["binary"]))
        return {"ok": True, "doc": {**doc, "packet": layers}, "warnings": [FALLBACK_WARNING, *warnings]}

    def _reconstruct(self, packet: list[dict[str, Any]], binary: str, edited: dict[str, Any]
                     ) -> list[dict[str, Any]]:
        """The packet with `edited["field"]` of layer `edited["layer"]` set from the edited bytes; _Fallback
        when `_reconstructed` fails or the bytes dissect to a different layer stack than the document."""
        from scapy.layers.l2 import Ether
        at, name = edited["layer"], edited["field"]
        if type(at) is not int or not 0 <= at < len(packet) or type(name) is not str:
            raise _Fallback
        data = base64.b64decode(binary)
        tail: list[dict[str, Any]] = []
        if len(data) > EDITOR_DECODE_BYTES:
            # A capped document: its last layer is the raw tail. Reconstruct the head only; an edit of a field
            # in the tail layer itself falls back to the byte edit, which `_layers_of` bounds.
            if at == len(packet) - 1 or not _is_tail(packet[-1], len(data) - EDITOR_DECODE_BYTES):
                raise _Fallback
            tail = [_raw_layer(data[EDITOR_DECODE_BYTES:])]
            packet, binary = packet[:-1], base64.b64encode(data[:EDITOR_DECODE_BYTES]).decode("ascii")
        model = [{**layer, "fields": [f for f in layer["fields"] if not (j == at and f["id"] == name)]}
                 for j, layer in enumerate(packet)]
        try:
            built = self._reconstructed(binary, model)
        except Exception as e:
            if _caused_by_memory(e):
                raise
            raise _Fallback from None
        stack = _chain(Ether(built))
        if [type(x).__name__ for x in stack] != [layer["id"] for layer in packet]:
            raise _Fallback
        kind = _option_layer(stack[at]) if name == "options" else None
        if kind is not None:
            # The edited bytes as they are, dissected and never rebuilt: `built` is Scapy's rebuild
            # of its reading, which rewrites a lossy TCP option (`1e 01` becomes `1e 02`).
            head = base64.b64decode(binary)
            value = _machine(option_bytes(kind, bytes(_chain(Ether(head))[at])))
        else:
            value = doc_value(stack[at].getfieldval(name))
        out = [{**layer, "fields": list(layer["fields"])} for layer in packet]
        fields = out[at]["fields"]
        for f in fields:
            if f["id"] == name:
                out[at]["fields"] = [{**f, "value": value} if f["id"] == name else f for f in fields]
                return [*out, *tail]
        fields.append({"id": name, "value": value})
        return [*out, *tail]

    def _reconstructed(self, binary: str, model: list[dict[str, Any]]) -> bytes:
        """The service's `reconstruct_pkt` for a model with no deleted layer, as bytes: the frame `binary` is
        dissected, each model layer's fields are set on the layer of the same position, and layers the frame
        lacks are appended from the model. The layers are walked with `_chain`: the service indexes with
        `pkt[depth]`, which Scapy's `getlayer` counts through an IP layer's option packets, so any frame
        with IP options raised "Protocol id inconsistent" and fell back. A model layer whose id differs
        from the frame's layer at its position is _Fallback."""
        from scapy.layers.l2 import Ether
        pkt = Ether(base64.b64decode(binary))
        layers = _chain(pkt)
        for depth, ml in enumerate(model):
            if depth >= len(layers):
                return bytes(pkt / self.svc._packet_model_to_scapy_packet(model[depth:]))
            if ml["id"] != type(layers[depth]).__name__:
                raise _Fallback
            if "fields" in ml:
                self.svc._modify_layer(pkt, layers[depth], ml["fields"])
        return bytes(pkt)

    def decompile(self, args: dict[str, Any]) -> dict[str, Any]:
        """`{instructions, global_parameters, warnings}` for the VM `args["vm"]` of the packet bytes
        `args["binary"]`."""
        return {"ok": True, **self._decompile(args["binary"], args["vm"])}

    def _decompile(self, binary: str, vm: dict[str, Any]) -> dict[str, Any]:
        """The service's `decompile_vm_raw`, as editor parameters: each kept value is a string (or a vtype
        object for a list), and a key the editor has no parameter for is dropped, with a warning when its
        value is not the instruction's default."""
        raw = self.svc.decompile_vm_raw(self.h, binary, vm)
        script = raw if isinstance(raw, dict) else {}
        metas = {m["id"]: m for m in self.svc.field_engine_instructions_meta}
        warnings: list[str] = []
        instructions: list[dict[str, Any]] = []
        for i, ins in enumerate(script.get("instructions", [])):
            iid, params = ins["id"], ins["parameters"]
            meta = metas.get(iid)
            if meta is None:
                warnings.append(f"unknown instruction {iid}")
                continue
            defaults = {k: p.default for k, p in
                        inspect.signature(self.svc._vm_instructions[iid].__init__).parameters.items()
                        if p.default is not inspect.Parameter.empty}
            kept: dict[str, Any] = {}
            for key, value in params.items():
                if key in meta["parameters"]:
                    text = _fe_value(value)
                    if text is None:
                        warnings.append(f"instruction {i} {iid}: {key}={value!r} is not editable here "
                                        "and was dropped")
                    else:
                        kept[key] = text
                elif key in defaults and value != defaults[key]:
                    warnings.append(f"instruction {i} {iid}: {key}={value!r} is not editable here and "
                                    "was dropped")
            instructions.append({"id": iid, "parameters": kept})
        glob = {k: _fe_value(v) for k, v in script.get("global_parameters", {}).items()}
        return {"instructions": instructions,
                "global_parameters": {k: v for k, v in glob.items() if v is not None},
                "warnings": warnings}

    def templates(self, args: dict[str, Any]) -> dict[str, Any]:
        """`{templates: [{id, name}]}`: the server's own templates, by id."""
        found = [{"id": t["id"], "name": t["meta"]["name"]} for t in self.svc.get_templates(self.h)]
        return {"ok": True, "templates": sorted(found, key=lambda t: t["id"])}

    def template(self, args: dict[str, Any]) -> dict[str, Any]:
        """`{data}`: the template file `args["id"]` as base64 text."""
        try:
            data = self.svc.get_template(self.h, {"id": args["id"]})
        except OSError:
            data = b""
        if not data:
            return {"ok": False, "error": f"unknown template {args['id']}"}
        return {"ok": True, "data": bytes(data).decode("ascii")}

    def pcap_list(self, args: dict[str, Any]) -> dict[str, Any]:
        """`{total, rows}`: the capture `args["path"]` counted, and rows `args["offset"]` to
        `+args["limit"]` as `{index, length, summary}`; a summary dissects the first SUMMARY_BYTES only. Reads
        one record at a time (plan D4)."""
        from scapy.layers.l2 import Ether
        offset, limit = int(args["offset"]), int(args["limit"])
        rows: list[dict[str, Any]] = []
        total = 0
        try:
            for data in _pcap_records(args["path"]):
                if offset <= total < offset + limit:
                    try:
                        summary = Ether(data[:SUMMARY_BYTES]).summary()
                    except Exception as e:
                        if _caused_by_memory(e):
                            raise
                        summary = ""
                    rows.append({"index": total, "length": len(data), "summary": summary})
                total += 1
        except _NotPcap as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "total": total, "rows": rows}

    def pcap_packet(self, args: dict[str, Any]) -> dict[str, Any]:
        """`{binary}`: record `args["index"]` of the capture `args["path"]`, base64."""
        want = int(args["index"])
        try:
            for i, data in enumerate(_pcap_records(args["path"])):
                if i == want:
                    return {"ok": True, "binary": base64.b64encode(data).decode("ascii")}
        except _NotPcap as e:
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": f"the capture has no packet {want}"}

    def to_stream(self, args: dict[str, Any]) -> dict[str, Any]:
        """`{packet: {binary, meta}, vm}` for the packet model and Field Engine model; a build or Field
        Engine error is the reply's error. The VM never carries `split_by_var` (plan D2)."""
        res = self.build(args)
        if res["error"]:
            return {"ok": False, "error": res["error"]}
        fe = res["field_engine"]
        if fe is not None and fe["error"]:
            return {"ok": False, "error": fe["error"]}
        vm = fe["instructions"] if fe is not None else {"instructions": []}
        return {"ok": True, "packet": {"binary": res["binary"], "meta": ""}, "vm": vm}

    def to_code(self, args: dict[str, Any]) -> dict[str, Any]:
        """`{code}`: the VM half of upstream `STLStream.to_code()` for the packet `args["binary"]` and the
        VM `args["vm"]`: the lines from `vm = STLVM()` up to the line before `stream = STLStream(`."""
        from trex.stl.api import STLPktBuilder, STLStream
        builder = STLPktBuilder.from_json({"packet": {"binary": args["binary"]}, "vm": args["vm"]})
        lines = STLStream(packet=builder).to_code().split("\n")
        start = lines.index("vm = STLVM()")
        stop = next(i for i, ln in enumerate(lines) if ln.startswith("stream = STLStream("))
        return {"ok": True, "code": "\n".join(lines[start:stop])}

    def _packet_only(self, packet: list[dict[str, Any]],
                     field_engine: dict[str, Any] | None) -> dict[str, Any]:
        """`build_pkt` alone, with `field_engine` as given; a failure is the result's `error` and, since the
        packet half is then what failed, the field-engine result is dropped."""
        try:
            res = self.svc.build_pkt(self.h, packet)
        except Exception as e:
            if _caused_by_memory(e):
                raise
            return {"ok": True, "binary": None, "data": [], "field_engine": None, "error": _describe(e)}
        return {"ok": True, "binary": res["binary"], "data": res["data"], "field_engine": field_engine,
                "error": None}


class _Fallback(Exception):
    """`_reconstruct` cannot keep the document field by field; the caller re-derives it from the bytes."""


class _NotPcap(Exception):
    """The file is not a capture the worker can read; the message is the whole error."""


class OptionBytes(bytes):
    """Option bytes zero-padded to 4 that a document's MACHINE value built, with the length of the document's
    own bytes. It rides on the value through `Packet.__div__`'s copies (`Field.do_copy` returns bytes as is)
    so `_retext` can show the document's bytes and how many zeros the build added."""

    document_length: int = 0


def _option_layer(layer: Any) -> OptionLayer | None:
    """`"IP"` for an IP-family layer (`IPerror` too), `"TCP"` for a TCP-family one, else None."""
    from scapy.layers.inet import IP, TCP
    return "IP" if isinstance(layer, IP) else "TCP" if isinstance(layer, TCP) else None


def option_bytes(kind: OptionLayer, layer_bytes: bytes) -> bytes:
    """The option bytes of an IP or TCP header: those between the fixed 20 bytes and the header's end, read
    with the header-length byte of `layer_bytes` (IP `ihl`, TCP `dataofs`), padding included."""
    if kind == "IP":
        end = (layer_bytes[0] & 0x0F) * 4 if layer_bytes else 0
    else:
        end = (layer_bytes[12] >> 4) * 4 if len(layer_bytes) > 12 else 0
    return layer_bytes[20:end]


def _machine(data: bytes) -> dict[str, str]:
    """`data` as a MACHINE value."""
    return {"vtype": "MACHINE", "base64": base64.b64encode(data).decode("ascii")}


def _document_options(layer: Any, kind: str) -> OptionBytes | None:
    """The bytes a document's MACHINE options gave the model `layer` (`OptionBytes`), else None."""
    from scapy.packet import Packet
    held = layer.fields.get("options")
    if kind == "IP":
        # An EXPRESSION may leave bytes, or a list of them, where the field holds packets.
        one = isinstance(held, list) and len(held) == 1 and isinstance(held[0], Packet) \
            and "load" in held[0].fields
        held = held[0].load if one else None
    return held if isinstance(held, OptionBytes) else None


def _retext(result: list[dict[str, Any]], model: list[Any], data: list[Any]) -> None:
    """Rewrite what the field tree `result` shows. A flags field with no flag set shows `0`, where Scapy's
    text is empty. Every IP- or TCP-family layer's `options` field gets `value` MACHINE, `options` (the
    decoded rows, `packet_options.decode`) and `hvalue` (`options_text`): the bytes are the document's own
    when it gave MACHINE options (`padded` is the zeros the build added), else those the wire carries, read
    from the layer dissected from the bytes (`data`, matched by class) or, past a class mismatch, from the
    model layer's own build."""
    from scapy.fields import FlagsField

    from trex_web_gui.trexapi.packet_options import decode, options_text
    for layer, m in zip(result, model, strict=False):
        by_id = {f["id"]: f for f in layer.get("fields", [])}
        for fd in m.fields_desc:
            f = by_id.get(fd.name)
            if f is not None and isinstance(fd, FlagsField) and f.get("hvalue") == "":
                f["hvalue"] = "0"
    for i, (layer, m) in enumerate(zip(result, model, strict=False)):
        kind = _option_layer(m)
        f = next((f for f in layer.get("fields", []) if f["id"] == "options"), None)
        if kind is None or f is None:
            continue
        held = _document_options(m, kind)
        if held is not None:
            shown, padded = bytes(held)[:held.document_length], len(held) - held.document_length
        else:
            b = data[i] if i < len(data) else m
            same = isinstance(m, type(b)) or isinstance(b, type(m))
            shown, padded = option_bytes(kind, bytes(b if same else m)), 0
        decoded = decode(kind, shown)
        decoded["padded"] = padded
        f["value"] = _machine(shown)
        f["options"] = decoded
        f["hvalue"] = options_text(kind, decoded)


def _chain(pkt: Any) -> list[Any]:
    """The layers of `pkt`, outermost first."""
    out = []
    while pkt:
        out.append(pkt)
        pkt = pkt.payload
    return out


def _raw_layer(data: bytes) -> dict[str, Any]:
    """A Raw document layer holding `data`."""
    return {"id": "Raw", "fields": [{"id": "load", "value": doc_value(data)}]}


def _dissect(raw: bytes, capped: bool) -> tuple[Any, list[Any], list[int]]:
    """`(pkt, layers, lengths)` of `raw`: dissected with `Ether` (only the first EDITOR_DECODE_BYTES when
    `capped`, the rest attached as an undissected Raw layer), its hidden defaults and derived fields removed;
    `lengths` are the layers' byte lengths before the caches were dropped."""
    from scapy.layers.l2 import Ether
    pkt = Ether(raw[:EDITOR_DECODE_BYTES] if capped else raw)
    layers = _chain(pkt)
    if capped:
        from scapy.packet import Raw
        layers[-1].add_payload(Raw(raw[EDITOR_DECODE_BYTES:]))
        layers = _chain(pkt)
    lengths = [len(x) for x in layers]                          # before the caches are dropped
    for layer in layers:
        layer.raw_packet_cache = None
    pkt.hide_defaults()
    # Judge the derived fields over the exact option bytes: Scapy rebuilds a lossy TCP option differently
    # (`1e 01` becomes `1e 02`), which would keep the frame's checksum in the document. An empty value is not
    # the default, so this follows `hide_defaults`: only a layer that has options still has the field.
    from scapy.config import conf
    for i, layer in enumerate(layers):
        kind = _option_layer(layer)
        if kind is not None and "options" in layer.fields:
            exact = option_bytes(kind, raw[len(raw) - min(lengths[i], len(raw)):])
            layer.fields["options"] = [conf.raw_layer(load=exact)] if kind == "IP" else exact
    _drop_derived(pkt, layers)
    return pkt, layers, lengths


def _is_tail(layer: dict[str, Any], length: int) -> bool:
    """True when `layer` is a Raw layer whose `load` is `length` bytes (a capped document's raw tail)."""
    if layer.get("id") != "Raw":
        return False
    for f in layer.get("fields", []):
        v = f.get("value")
        if f.get("id") == "load" and isinstance(v, dict) and v.get("vtype") == "BYTES" and "base64" in v:
            return len(base64.b64decode(v["base64"])) == length
    return False


def _drop_derived(pkt: Any, layers: list[Any]) -> None:
    """Delete every field whose class default is None (a length, checksum or offset the build computes)
    when the packet's bytes do not change without it."""
    for x in layers:
        x.raw_packet_cache = None
    want = bytes(pkt)
    for layer in layers:
        for name in list(layer.fields):
            if layer.get_field(name).default is not None:
                continue
            old = layer.fields.pop(name)
            if bytes(pkt) != want:
                layer.fields[name] = old


def _fe_value(v: Any) -> Any:
    """A decompiled instruction value as an editor parameter: a string (`str(int)`, `true`/`false`,
    `None`), a vtype EXPRESSION for a list that is a literal, None when it can be neither."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float | str) or v is None:
        return str(v)
    if isinstance(v, list | tuple) and (expr := literal_expr(list(v))) is not None:
        return {"vtype": "EXPRESSION", "expr": expr}
    return None


def _pcap_records(path: str) -> Any:
    """Yield each record's bytes of the capture `path`, one at a time; _NotPcap when it is not a capture."""
    from scapy.error import Scapy_Exception
    from scapy.utils import RawPcapReader
    try:
        reader = RawPcapReader(path)
    except Exception as e:
        if _caused_by_memory(e):
            raise
        raise _NotPcap(f"not a pcap: {e}") from None
    try:
        for data, _meta in reader:
            yield data
    except (OSError, ValueError, EOFError, IndexError, struct.error, Scapy_Exception) as e:
        raise _NotPcap(f"not a pcap: {e}") from None
    finally:
        reader.close()


def _describe(e: BaseException) -> str:
    """`"<Type>: <message>"` for an exception."""
    return f"{type(e).__name__}: {e}"


def main() -> int:
    """Worker body; returns the exit code (0 on stdin EOF).

    Order matters: the parent-death signal and the parent check come first (the bootstrap already set
    RLIMIT_AS), then the watchdog thread, then the result fd is duplicated and fds 1 and 2 go to /dev/null,
    then the service is built and the ready line written."""
    pdeathsig = set_parent_death_signal()
    if parent_gone():
        return 1
    threading.Thread(target=_watch_parent, daemon=True).start()
    out = os.fdopen(os.dup(1), "w")
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)
    sys.stdout = sys.stderr = open(os.devnull, "w")
    engine = Engine()
    out.write(json.dumps({"ready": True, "pdeathsig": pdeathsig}) + "\n")
    out.flush()
    while True:
        line = sys.stdin.readline()
        if not line:
            return 0
        rid: Any = None
        oom_line = json.dumps({"id": None, "ok": False, "error": "MemoryError"}) + "\n"
        try:
            req = json.loads(line)
            rid = req["id"]
            oom_line = json.dumps({"id": rid, "ok": False, "error": "MemoryError"}) + "\n"
            op = engine.ops.get(req["op"])
            if op is None:
                res: dict[str, Any] = {"ok": False, "error": f"unknown operation {req['op']!r}"}
            else:
                res = op(req.get("args") or {})
            text = json.dumps({"id": rid, **res}) + "\n"
        except BaseException as e:
            if _caused_by_memory(e):
                text = oom_line
            else:
                try:
                    text = json.dumps({"id": rid, "ok": False, "error": _describe(e)}) + "\n"
                except MemoryError:
                    text = oom_line
        out.write(text)
        out.flush()


if __name__ == "__main__":
    sys.exit(main())
