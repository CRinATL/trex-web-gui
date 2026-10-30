"""Subprocess entry point: run one profile operation and print one JSON result line.
Run by profiles.py as: python -I -B -c <bootstrap: an address-space limit, then this module as __main__>
(request JSON on stdin). Never imported by the backend process: .py profiles are arbitrary code (plan §4.3),
and pcap, YAML and JSON input is untrusted.

The request's `op` picks the operation: `load` (the default: load a profile file for each requested port),
`pcap` (import a capture), `to_python` (`dump_to_code` of the given streams), `round_trip` (the given
streams through `from_json` and `to_json`) and `template` (the default new stream).

The result goes to a DUP of the original stdout fd, taken before any operation runs; fds 1 and 2 are then
pointed at /dev/null, so nothing the profile does (os.write(1), sys.__stdout__, C extensions, child
processes) can reach the result pipe. sys.stdout/sys.stderr go to a capped buffer for error messages."""
import argparse
import base64
import io
import ipaddress
import json
import os
import sys
import traceback
from typing import Any

CAP = 64 * 1024            # characters of profile output kept for error messages
# Streams summarised for the UI. Ether(raw).summary() measured 0.45 ms/stream (2026-10-16), so an
# uncapped 22k-stream profile would spend the whole 10 s load budget on the summary alone.
SUMMARY_CAP = 500


class Refused(Exception):
    """Input the operation refuses; its message is the whole error, sent without a traceback."""


def main() -> int:
    """Worker body: read the request from stdin, run its operation, and write one JSON result line to the
    saved stdout. Points fds 1 and 2 at /dev/null and replaces sys.stdout and sys.stderr. Returns 0 once a
    result line is written, ok or not; a bad request on stdin raises before that.

    A MemoryError (the bootstrap's address-space limit) writes a line built before the work starts, because
    formatting a traceback could itself fail under the limit; the parent names the limit it set."""
    out = os.fdopen(os.dup(1), "w")
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)
    noise = io.StringIO()
    oom_line = json.dumps({"ok": False, "error": "MemoryError"}) + "\n"

    class Capped(io.TextIOBase):
        def write(self, s: str) -> int:
            """Keep what the profile prints, up to CAP characters, for error messages; returns len(s)."""
            if noise.tell() < CAP:
                noise.write(s[: CAP - noise.tell()])
            return len(s)

    sys.stdout = sys.stderr = Capped()
    req = json.loads(sys.stdin.read())
    tunable_error: list[str] = []
    try:
        from trex_web_gui.trexapi.loader import load
        load()
        op = req.get("op", "load")
        if op == "load":
            res = _op_load(req, noise, tunable_error)
        elif op == "pcap":
            res = _op_pcap(req)
        elif op == "to_python":
            from trex.stl.api import STLProfile
            res = {"ok": True, "code": STLProfile.from_json(req["streams"]).dump_to_code()}
        elif op == "round_trip":
            from trex.stl.api import STLProfile
            res = {"ok": True, "streams": STLProfile.from_json(req["streams"]).to_json()}
        elif op == "template":
            res = {"ok": True, "stream": _op_template()}
        else:
            raise Refused(f"unknown operation {op!r}")
        out.write(json.dumps(res) + "\n")
    except MemoryError:
        out.write(oom_line)
    except BaseException as e:
        if _caused_by_memory(e):                           # load_py wraps a MemoryError in TRexError
            out.write(oom_line)
        elif tunable_error:                                # set by capture(); load_py wrapped it in TRexError
            out.write(json.dumps({"ok": False, "error": f"bad tunables: {tunable_error[-1]}"}) + "\n")
        elif isinstance(e, Refused):
            out.write(json.dumps({"ok": False, "error": str(e)}) + "\n")
        else:
            try:
                tb = traceback.format_exc()
                line = json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}",
                                   "traceback": tb[-CAP:], "output": noise.getvalue()[-4096:]}) + "\n"
            except MemoryError:
                line = oom_line
            out.write(line)
    out.flush()
    return 0


def _caused_by_memory(e: BaseException) -> bool:
    """True when the failure is running out of memory: e is a MemoryError, or e is the TRexError load_py
    raises in its `except Exception` around a MemoryError (trex_stl_streams.py:1188-1194 "raise
    TRexError(summary)"), whose __context__ is then that MemoryError itself. An error raised while some
    MemoryError was being handled (a profile that catches one and raises its own) is not."""
    if isinstance(e, MemoryError):
        return True
    try:
        from trex.common.trex_exceptions import TRexError
    except BaseException:                          # the vendored client never loaded, so load_py never ran
        return False
    return type(e) is TRexError and isinstance(e.__context__, MemoryError)


def _op_load(req: dict[str, Any], noise: io.StringIO, tunable_error: list[str]) -> dict[str, Any]:
    """The `load` operation: load req["path"] for each of req["ports"] with req["tunables"], patching
    ArgumentParser.parse_args to record the profile's tunables. An argparse error is appended to
    `tunable_error` before it is raised."""
    from scapy.layers.l2 import Ether
    from trex.stl.api import STLProfile

    captured: list[list[dict[str, Any]]] = []
    orig = argparse.ArgumentParser.parse_args

    def capture(self: argparse.ArgumentParser, args: Any = None, namespace: Any = None) -> Any:
        """Stand-in for ArgumentParser.parse_args: record the parser's options as tunables, then parse.
        An argparse error becomes a ValueError carrying its message, because load_py would swallow the
        SystemExit as "--help"; a --help exit is re-raised."""
        acts = []
        for act in self._actions:
            if isinstance(act, argparse._HelpAction):
                continue
            if isinstance(act, argparse._StoreTrueAction):
                kind = "bool_true"
            elif isinstance(act, argparse._StoreFalseAction):
                kind = "bool_false"
            elif act.choices is not None:
                kind = "choice"
            elif act.nargs not in (None, "?") or not act.option_strings:
                kind = "raw"
            else:
                kind = {int: "int", float: "float"}.get(act.type, "str")  # type: ignore[call-overload]
            default = act.default if _jsonable(act.default) else repr(act.default)
            acts.append({"dest": act.dest, "flag": act.option_strings[0] if act.option_strings else "",
                         "kind": kind, "default": default,
                         "choices": list(act.choices) if act.choices is not None else None,
                         "help": act.help or ""})
        captured.append(acts)
        try:
            return orig(self, args, namespace)
        except SystemExit as e:
            # load_py turns ANY SystemExit into "return None" (it assumes --help,
            # trex_stl_streams.py:1179-1181), so an argparse error must leave as a different exception.
            if e.code == 0:
                raise
            lines = noise.getvalue().strip().splitlines()
            # argparse prints "<prog>: error: <msg>"; prog is "-c" under the bootstrap (measured)
            msg = lines[-1].split(": error: ", 1)[-1] if lines else f"argparse exit {e.code}"
            tunable_error.append(msg)
            raise ValueError(tunable_error[-1]) from None

    argparse.ArgumentParser.parse_args = capture  # type: ignore[method-assign]
    path, ports, tunables = req["path"], req["ports"], req["tunables"]
    suffix = os.path.splitext(path)[1].lower()
    by_port, kind = {}, {"py": "py", "yaml": "yaml", "yml": "yaml", "json": "json"}[suffix[1:]]
    for pid in ports:
        if kind == "py":
            prof = STLProfile.load_py(path, direction=pid % 2, port_id=pid, tunables=tunables)
            if prof is None:
                raise RuntimeError("the profile asked for --help; remove it from the tunables")
        elif kind == "yaml":
            prof = STLProfile.load_yaml(path)
        else:
            prof = STLProfile.load_json(path)
        by_port[str(pid)] = prof.to_json()
    first = by_port[str(ports[0])]
    summary = [_summary(i, s, Ether) for i, s in enumerate(first[:SUMMARY_CAP])]
    return {"ok": True, "kind": kind, "tunables": captured[0] if captured else [],
            "streams_by_port": by_port, "summary": summary, "stream_count": len(first)}


def _op_pcap(req: dict[str, Any]) -> dict[str, Any]:
    """The `pcap` operation: count the records of req["path"] without holding them and refuse more than
    req["streams_max"]; with an address rewrite, refuse a packet without IPv4; then build the streams with
    `STLProfile.load_pcap` and the rewrite VM, and apply the name prefix to every name and `next`."""
    from scapy.layers.inet import IP
    from scapy.layers.l2 import Ether
    from scapy.utils import RawPcapReader
    from trex.stl.api import STLProfile, STLVmFixIpv4, STLVmFlowVar, STLVmWrFlowVar

    path, opts, most = req["path"], req["opts"], req["streams_max"]
    reader = RawPcapReader(path)
    try:
        n = 0
        for _record in reader:                     # one record at a time, unlike load_pcap's read_all()
            n += 1
            if n > most:
                raise Refused(f"the capture holds more than {most} packets; a profile holds at most "
                              f"{most} streams")
    finally:
        reader.close()
    vm: list[Any] = []
    for key, field in (("rewrite_src", "src"), ("rewrite_dst", "dst")):
        rw = opts.get(key)
        if rw:
            start = int(ipaddress.IPv4Address(rw["start"]))
            vm += [STLVmFlowVar(name=f"ip_{field}", min_value=start, max_value=start + rw["count"] - 1,
                                size=4, op="inc"),
                   STLVmWrFlowVar(fv_name=f"ip_{field}", pkt_offset=f"IP.{field}")]
    if vm:
        vm.append(STLVmFixIpv4(offset="IP"))
        reader = RawPcapReader(path)
        try:
            for i, (raw, _meta) in enumerate(reader, start=1):
                if IP not in Ether(raw):
                    raise Refused(f"packet {i} has no IPv4 header; the address rewrite needs IPv4")
        finally:
            reader.close()
    prof = STLProfile.load_pcap(path, opts["ipg_usec"], opts["speedup"], opts["loop_count"], vm or None)
    streams = prof.to_json()
    prefix = opts.get("name_prefix") or ""
    if prefix:
        for s in streams:
            s["name"] = f"{prefix}{s['name']}"
            if s.get("next") is not None:
                s["next"] = f"{prefix}{s['next']}"
    return {"ok": True, "streams": streams}


def _op_template() -> dict[str, Any]:
    """The `template` operation: the default new stream, a 60-byte UDP packet (64 with FCS) at 1 pps,
    unnamed, in its JSON form."""
    from scapy.layers.inet import IP, UDP
    from scapy.layers.l2 import Ether
    from trex.stl.api import STLPktBuilder, STLStream, STLTXCont

    pkt = Ether() / IP(src="16.0.0.1", dst="48.0.0.1") / UDP(dport=12, sport=1025) / ("x" * 18)
    stream: dict[str, Any] = STLStream(packet=STLPktBuilder(pkt=pkt), mode=STLTXCont(pps=1)).to_json()
    return stream


def _jsonable(v: object) -> bool:
    """True when json.dumps accepts v."""
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


def _rate(mode: dict[str, Any]) -> str:
    """A stream mode's rate as text, e.g. "100 pps" or "10 %"."""
    r = mode.get("rate") or {}
    units = {"pps": "pps", "bps_L1": "bps L1", "bps_L2": "bps L2", "percentage": "%"}
    unit = units.get(str(r.get("type")), r.get("type"))
    v = r.get("value")
    return f"{v:g} {unit}" if isinstance(v, (int, float)) else str(unit)


def _summary(i: int, s: dict[str, Any], Ether: Any) -> dict[str, Any]:
    """The StreamSummary for stream i from its JSON form; Ether is Scapy's, passed in by the caller."""
    raw = base64.b64decode(s["packet"]["binary"])
    vm = (s.get("vm") or {}).get("instructions") or []
    return {"index": i, "name": s.get("name"), "enabled": bool(s.get("enabled", True)),
            "self_start": bool(s.get("self_start", True)), "mode": s["mode"]["type"],
            "rate": _rate(s["mode"]), "packet": Ether(raw).summary(), "length": len(raw),
            "next": s.get("next"), "has_vm": bool(vm)}


if __name__ == "__main__":
    sys.exit(main())
