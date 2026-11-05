"""A fake TRex EMU server (JSON-RPC over a ZMQ ROUTER, plain JSON replies): `fake_emu.py <port> [flags]`.

It serves the handshake (api_sync_v2 for EMU 1.0 and 1.1, get_version, ping), the three error shapes
recorded in tests/fixtures/p7/negatives.json (wrong API version -32600, missing or stale api_h -32602,
unknown method -32601) and the reads: ctx_iter and ctx_client_iter (cursors with `reset`, `count`,
`stopped`/`empty` and the epoch error), ctx_get_info, ctx_client_get_info, ctx_cnt and every plugin's
counters, answered from tests/fixtures/p7/counters.json (meta, sparse values, zero-filled values, the
mask on value requests; a meta request ignores mask, as the shipped server does, ruling R29).
The texts are read from those files, never retyped here: the unknown-key, duplicate and client-scope 'no
valid plugin' errors are the e3_* entries of negatives.json, and the writes' partial effects follow the E3
steps of summary.json.

The writes of a profile load and removal (ctx_set_def_plugins, ctx_add, ctx_client_set_def_plugins,
ctx_client_add, ctx_client_remove, ctx_remove) change the state the reads serve, and each one bumps the epoch
(ruling R10: another client's add or remove interrupts an open cursor).

Known divergences from the real server:
- No packets, so counters never advance; a counter table is the recorded one whatever the namespace.
  [U:emu-counters-static]
- An error text that names the key it failed on (a vport, a MAC) is the recorded text with that key
  swapped for the asked one; only the vport (and the colon-form MAC) is substituted, so the VLAN part of the
  recorded key rendering stays as recorded. [U:emu-key-rendering]
- A ctx_add, ctx_remove or ctx_client_add that answers an error after part of it took effect bumps the epoch
  once, and one that changed nothing bumps none. [U:emu-partial-write-epoch]
- ctx_remove with a tunnel repeated in the request answers the unknown-tunnel error for the repeat, and a
  namespace holding clients that is not the first tunnel leaves the removals before it done.
  [U:emu-remove-repeat-error] [U:emu-remove-keeps-before-refusal]
- ctx_client_remove with several MACs removes none when one is unknown: E3 sent one MAC at a time.
  [U:emu-client-remove-atomic]
- ctx_client_get_info for a MAC copies the recorded client record and synthesises its ipv4 from the MAC, not
  the address the client was added with. [U:emu-client-info-synth]
- Removing a namespace that loaded the ipfix plugin does not panic the way trex-emu does (use --hang
  ctx_remove to model the unanswered request). [U:emu-ipfix-no-panic]
- Its texts were recorded from standalone trex-emu, and the app talks to the EMU that TRex --emu hosts.
  [U:emu-standalone-recording]

The socket is a ROUTER, so a request that is not answered (--hang) does not block the next one: the client
rebuilds its REQ socket after a timeout, as the real server's would let it.

A SIGUSR2 restart is applied before the next message: a new api_h and empty state. A request
carrying the old api_h then answers -32602, as the real server does (pinned under TRex
[U:emu-restart-model]).

Seeding: --foreign-ns K starts with K namespaces (vport 0..K-1), --clients C gives each C clients,
--plugins a,b names the plugins loaded in every namespace and client (default arp,icmp; the server's own
names, so transe for transporte).
--interrupt-iter N bumps the epoch after every Nth cursor read, as another client's ctx_add would, so the
next page of any open cursor answers the recorded "interupted" error (a reset starts a fresh cursor);
--interrupt-limit K stops after K bumps. --repeat-iter N makes the second page of N cursor walks repeat
the last item already sent (-1: every walk), as a server whose list changed under the cursor might.
--log PATH appends one JSON line per request (method and params without api_h),
which the fake_emu fixture reads back as FakeEmu.requests().
Flags: --fail METHOD=N (a made-up -32600 error for the Nth request of METHOD) [U:emu-hook-injected-failure],
--hang METHOD (never answer it), --huge-plain METHOD (a 9 MiB plain reply, over any client's
MAXMSGSIZE, which libzmq drops), --huge-zip METHOD (a small zlib frame whose length field claims 9 MiB).
A `shutdown` request is never answered: the fake prints `[fake-emu] FORBIDDEN shutdown` (the fake_emu fixture
fails the test at teardown when it saw that line).
The first line is `[fake-emu] ready port=N`."""
import argparse
import copy
import json
import re
import secrets
import signal
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

import zmq

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "p7"
MAGIC = 0xABE85CEA
HUGE_BYTES = 9 * 1024 * 1024
CHUNK_MAX = 255      # the server's most items per page
SERVER_API = ("EMU", 1, 1)
ACCEPTED_API = {("EMU", 1, 0), ("EMU", 1, 1)}      # both answered with an api_h (summary.json "handshake")

CNT = json.loads((FIX / "counters.json").read_text())
_RPC = [json.loads(line) for line in (FIX / "rpc.jsonl").read_text().splitlines()]
CLIENT_INFO = next(r["resp"]["result"][0] for r in _RPC if r["req"]["method"] == "ctx_client_get_info")
NEG = json.loads((FIX / "negatives.json").read_text())
SUM = json.loads((FIX / "summary.json").read_text())
VERSION = SUM["version"]
PING = SUM["ping"]
NOT_FOUND = NEG["ctx_stop_autotriggers_not_found"]["resp"]["error"]
BAD_API_H = NEG["ping_bogus_api_h"]["resp"]["error"]
MISMATCH_TEXT = NEG["api_sync_v2_stl"]["resp"]["error"]["message"]
MISMATCH_CODE = NEG["api_sync_v2_stl"]["resp"]["error"]["code"]
MISMATCH_FMT = MISMATCH_TEXT.replace("STL:(5:1)", "{name}:({major}:{minor})")
assert MISMATCH_FMT != MISMATCH_TEXT
_I = SUM["interrupted_iteration"]["after_add_by_other"]["error"]
INTERRUPTED = {"code": _I["code"], "message": _I["text"]}
NO_PLUGIN_TUNNEL = NEG["plugin_counters_not_loaded"]["resp"]["error"]["message"].replace("igmp", "{plugin}")
assert "{plugin}" in NO_PLUGIN_TUNNEL
NO_PLUGIN_CLIENT = NEG["e3_client_counter_plugin_not_loaded"]["resp"]["error"]["message"].replace(
    "dhcp", "{plugin}")
assert "{plugin}" in NO_PLUGIN_CLIENT
CODE_INVALID = -32600

# command -> (plugin key, scope, recorded entry) for every plugin counter in counters.json
PLUGIN_CMDS: dict[str, tuple[str, str, dict[str, Any]]] = {
    entry["cmd"]: (name, scope, entry) for name, plug in CNT["plugins"].items()
    for scope, entry in plug.items() if isinstance(entry, dict)}
SERVER_NAME = {name: plug["server_name"] for name, plug in CNT["plugins"].items()}

WRITES = {"ctx_set_def_plugins", "ctx_add", "ctx_client_set_def_plugins", "ctx_client_add",
          "ctx_client_remove", "ctx_remove"}

state: dict[str, Any] = {"api_h": secrets.token_urlsafe(8), "restart": False, "ns": [], "epoch": 0,
                         "reads": 0, "bumps": 0, "cursors": {}, "cleared": set(), "plugins": ["arp", "icmp"]}
opts = argparse.Namespace(interrupt_iter=0, interrupt_limit=None, repeat_iter=0)


def colon(mac: Any) -> str:
    """A MAC as the server renders it in a text: lowercase hex pairs joined by colons."""
    return ":".join(f"{b:02x}" for b in mac)


def recorded_error(name: str, params: dict[str, Any]) -> dict[str, Any]:
    """{"code", "message"} of the recorded error `name` (a negatives.json key), naming the key that failed.
    `params` is the failing item: {"vport": v} and/or {"mac": [..]}; a key that is absent or None (a request
    that omitted it) substitutes nothing, and the recorded text is answered as recorded. A recorded vport that
    occurs once in the text as a whole number is replaced with the asked one, and so is a recorded MAC in
    colon form; a text that names neither is answered as recorded.
    Never str.format: the recorded key rendering holds braces."""
    rec = NEG[name]
    text = rec["resp"]["error"]["message"]
    asked = rec["req"]["params"]
    tuns = [*(asked.get("tunnels") or []), *([asked["tun"]] if asked.get("tun") else [])]
    vports = {t["vport"] for t in tuns if len(re.findall(rf"\b{t['vport']}\b", text)) == 1}
    if len(vports) == 1 and params.get("vport") is not None:
        text = re.sub(rf"\b{vports.pop()}\b", str(params["vport"]), text)      # [U:emu-key-rendering]
    macs = [*(c["mac"] for c in asked.get("clients") or []), *(asked.get("macs") or []),
            *([asked["mac"]] if asked.get("mac") else [])]
    named = {colon(m) for m in macs if text.count(colon(m)) == 1}
    if len(named) == 1 and params.get("mac") is not None:
        text = text.replace(named.pop(), colon(params["mac"]))
    return {"code": rec["resp"]["error"]["code"], "message": text}


def decided(step: str) -> dict[str, Any] | None:
    """The recorded outcome of E3 step `step`, or None when that step or an earlier one went unanswered (a
    step after a crash was recorded against a restarted server and decides nothing)."""
    for st in SUM["e3"]["steps"]:
        if "unanswered" in st["outcome"]:
            return None
        if st["name"] == step:
            return dict(st["outcome"])
    return None


def listing(step: str) -> list[Any] | None:
    """The items a recorded E3 listing step showed, or None when it decides nothing (see `decided`)."""
    out = decided(step)
    return None if out is None else list((out.get("result") or {}).get("data") or [])


def _recorded_params(step: str) -> dict[str, Any]:
    """The params the recorder sent in E3 step `step` (summary.json is the one source of the scratch keys)."""
    return next(st for st in SUM["e3"]["steps"] if st["name"] == step)["params"]


SCRATCH_ADDED = _recorded_params("e3_ctx_add_duplicate_in_request")["tunnels"][0]["vport"]
SCRATCH_KEPT = _recorded_params("e3_ctx_add_new_and_existing")["tunnels"][0]["vport"]
SCRATCH_REMOVED = _recorded_params("e3_setup_ctx_add_empty")["tunnels"][0]["vport"]
REPEATED_MAC = _recorded_params("e3_ctx_client_add_duplicate_in_request")["clients"][0]["mac"]


def _ns_listed(step: str, vport: int) -> bool:
    """False only when the decided listing `step` shows no namespace `vport`."""
    items = listing(step)
    return items is None or any(i["vport"] == vport for i in items)


_RM_LISTED = listing("e3_ctx_iter_after_remove_known_and_unknown")
_CLIENT_ITER = listing("e3_ctx_client_iter_after_duplicate_in_request")
_REMOVE_OUT = decided("e3_ctx_remove_with_clients")
_INFO_OUT = decided("e3_ctx_get_info_known_and_unknown")
_MAC_OUT = decided("e3_ctx_client_get_info_unknown_mac")
# ctx_add keeps the namespaces added before the failing one, unless a decided listing lacks them
ADD_KEEPS = (_ns_listed("e3_ctx_iter_after_duplicate_in_request", SCRATCH_ADDED)
             and _ns_listed("e3_ctx_iter_after_new_and_existing", SCRATCH_KEPT))
# ctx_remove removes in order up to the first unknown tunnel when the removed scratch namespace is gone
# from the listing after it
REMOVE_IN_ORDER = _RM_LISTED is not None and not any(i["vport"] == SCRATCH_REMOVED for i in _RM_LISTED)
# a namespace that still holds clients is refused when the recorded removal is an error
REMOVE_REFUSES_CLIENTS = _REMOVE_OUT is not None and "error" in _REMOVE_OUT
# ctx_client_add keeps the clients added before a repeated MAC when the decided listing shows the repeat's MAC
CLIENT_ADD_KEEPS = _CLIENT_ITER is not None and REPEATED_MAC in _CLIENT_ITER
# ctx_get_info fails the whole request unless the recorded known-and-unknown request answered a result
GET_INFO_FAILS = _INFO_OUT is None or "error" in _INFO_OUT
# ctx_client_get_info refuses an unheld MAC when the recorded request is an error, else it skips the MAC
CLIENT_INFO_REFUSES = _MAC_OUT is not None and "error" in _MAC_OUT


def seed(count: int, clients: int) -> None:
    """Start with `count` namespaces (vport 0..count-1), each holding `clients` clients."""
    state["ns"] = [{"key": (v, (0, 0), (0, 0)),
                    "macs": [(0, 0, v >> 8, v & 255, c >> 8, c & 255) for c in range(clients)]}
                   for v in range(count)]


def on_restart(_sig: int, _frame: Any) -> None:
    """SIGUSR2: only flags the restart; the request loop applies it before the next message."""
    state["restart"] = True


def reply(req_id: Any, *, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    """A JSON-RPC reply object."""
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    return body


def key_of(t: dict[str, Any]) -> tuple[Any, tuple[Any, ...], tuple[Any, ...]]:
    """The namespace key a `tun` object or tunnel entry names."""
    return (t.get("vport"), tuple(t.get("tci") or (0, 0)), tuple(t.get("tpid") or (0, 0)))


def write(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """One write: {"result": True} or {"error": ...}. A change of the namespace or client lists bumps the
    epoch, which interrupts every open cursor."""
    if method == "ctx_set_def_plugins":
        state["def_ns_plugs"] = params.get("def_plugs") or {}
        return {"result": True}
    if method == "ctx_add":
        return add_namespaces(params.get("tunnels") or [])
    if method == "ctx_remove":
        return remove_namespaces(params.get("tunnels") or [])
    ns = find_ns(params)
    if ns is None:
        unknown = {"ctx_client_set_def_plugins": "e3_ctx_client_set_def_plugins_unknown_tunnel",
                   "ctx_client_add": "e3_ctx_client_add_unknown_tunnel",
                   "ctx_client_remove": "e3_ctx_client_remove_unknown_tunnel"}[method]
        return {"error": recorded_error(unknown, {"vport": (params.get("tun") or {}).get("vport")})}
    if method == "ctx_client_set_def_plugins":
        ns["def_c_plugs"] = params.get("def_plugs") or {}
        return {"result": True}
    if method == "ctx_client_add":
        return add_clients(ns, [tuple(c.get("mac") or ()) for c in params.get("clients") or []])
    macs = [tuple(m) for m in params.get("macs") or []]      # ctx_client_remove
    for m in macs:
        if m not in ns["macs"]:      # [U:emu-client-remove-atomic] none is removed when one is unknown
            return {"error": recorded_error("e3_ctx_client_remove_unknown_mac", {"mac": m})}
    ns["macs"] = [m for m in ns["macs"] if m not in macs]
    state["epoch"] += 1
    return {"result": True}


def add_namespaces(tunnels: list[dict[str, Any]]) -> dict[str, Any]:
    """ctx_add: a tunnel that exists, or repeats within the request, answers the recorded duplicate error. The
    tunnels before it stay added when the E3 listings show they do (ADD_KEEPS); either way an add bumps the
    epoch once, if it added any."""
    new: list[dict[str, Any]] = []
    error = None
    for t in tunnels:
        in_request = any(n["key"] == key_of(t) for n in new)
        if in_request or find_ns({"tun": t}):
            name = "e3_ctx_add_duplicate_in_request" if in_request else "e3_ctx_add_duplicate"
            error = {"error": recorded_error(name, {"vport": t.get("vport")})}
            break
        new.append({"key": key_of(t), "macs": [], "plugs": list(t.get("plugs") or {})})
    if error and not ADD_KEEPS:
        new = []
    state["ns"].extend(new)
    state["epoch"] += bool(new)      # [U:emu-partial-write-epoch]
    return error or {"result": True}


def remove_namespaces(tunnels: list[dict[str, Any]]) -> dict[str, Any]:
    """ctx_remove: an unknown tunnel, or one that still holds clients when the E3 removal refused it, answers
    its recorded error. The tunnels before it are removed when the E3 listing shows the scratch namespace gone
    (REMOVE_IN_ORDER), else none is."""
    gone: list[Any] = []
    error = None
    for t in tunnels:
        ns = find_ns({"tun": t})
        repeated = ns is not None and ns["key"] in gone
        if ns is None or (repeated and REMOVE_IN_ORDER):      # [U:emu-remove-repeat-error]
            error = {"error": recorded_error("e3_ctx_remove_unknown_tunnel", {"vport": t.get("vport")})}
            break
        if ns["macs"] and REMOVE_REFUSES_CLIENTS:      # [U:emu-remove-keeps-before-refusal] earlier ones go
            error = {"error": recorded_error("e3_ctx_remove_with_clients", {"vport": t.get("vport")})}
            break
        gone.append(ns["key"])
    if error and not REMOVE_IN_ORDER:
        gone = []
    state["ns"] = [n for n in state["ns"] if n["key"] not in gone]
    state["epoch"] += bool(gone)      # [U:emu-partial-write-epoch]
    return error or {"result": True}


def add_clients(ns: dict[str, Any], macs: list[tuple[Any, ...]]) -> dict[str, Any]:
    """ctx_client_add: a MAC the namespace holds, or repeated within the request, answers the recorded
    duplicate error. The MACs before it stay added when the E3 client listing shows the repeat's MAC
    (CLIENT_ADD_KEEPS)."""
    new: list[tuple[Any, ...]] = []
    error = None
    for m in macs:
        in_request = m in new
        if in_request or m in ns["macs"]:
            name = "e3_ctx_client_add_duplicate_in_request" if in_request else "e3_ctx_client_add_duplicate"
            error = {"error": recorded_error(name, {"mac": m})}
            break
        new.append(m)
    if error and not CLIENT_ADD_KEEPS:
        new = []
    ns["macs"].extend(new)
    state["epoch"] += bool(new)      # [U:emu-partial-write-epoch]
    return error or {"result": True}


def find_ns(params: dict[str, Any]) -> dict[str, Any] | None:
    """The namespace a request's `tun` names, or None."""
    t = params.get("tun") or {}
    key = (t.get("vport"), tuple(t.get("tci") or (0, 0)), tuple(t.get("tpid") or (0, 0)))
    return next((n for n in state["ns"] if n["key"] == key), None)


def wire_key(ns: dict[str, Any]) -> dict[str, Any]:
    """A namespace's key as a listing entry carries it."""
    v, tci, tpid = ns["key"]
    return {"vport": v, "tpid": list(tpid), "tci": list(tci), "plugs": None}


def iterate(params: dict[str, Any], cursor_key: Any, items: list[Any]) -> dict[str, Any]:
    """One page of a cursor over `items`. A reset opens the cursor at the current epoch; a page on a cursor
    opened before an epoch bump (or never opened) is the interrupted error. Then the read counts toward
    --interrupt-iter."""
    out: dict[str, Any]
    cur = state["cursors"].get(cursor_key)
    if params.get("reset"):
        cur = state["cursors"][cursor_key] = {"epoch": state["epoch"], "at": 0}
        out = {"empty": not items, "stopped": False, "data": None}
    elif cur is None or cur["epoch"] != state["epoch"]:
        out = {"error": INTERRUPTED}
    else:
        out = {"empty": False, "stopped": False, "data": None}
    if "error" not in out:
        page = items[cur["at"]:cur["at"] + int(params.get("count") or CHUNK_MAX)]
        cur["at"] += len(page)
        if page and cur["at"] > len(page) and opts.repeat_iter and not cur.get("repeated"):
            cur["repeated"] = True      # once per walk: the page starts with the item the last page ended on
            opts.repeat_iter -= opts.repeat_iter > 0
            page = [items[cur["at"] - len(page) - 1], *page]
        if page:
            out["data"] = page
        elif not out["empty"]:
            out["stopped"] = True
    state["reads"] += 1
    n = opts.interrupt_iter
    limit = opts.interrupt_limit
    if n and state["reads"] % n == 0 and (limit is None or state["bumps"] < limit):
        state["epoch"] += 1
        state["bumps"] += 1
    return out


def filtered(tables: dict[str, Any], mask: Any) -> dict[str, Any]:
    """`tables` limited to the group names in `mask` (all of them for a null mask)."""
    return {k: v for k, v in tables.items() if mask is None or k in mask}


def counters(rec: dict[str, Any], params: dict[str, Any], cmd: str) -> dict[str, Any]:
    """The recorded meta, sparse values or zero-filled values of one counter table set. A `clear` request
    zeroes that command's values from then on (the "never clears" test reads the request log)."""
    if params.get("clear"):
        state["cleared"].add(cmd)
    if params.get("meta"):
        return dict(rec["meta"])    # R29: the server ignores mask [U:emu-meta-ignores-mask]
    if params.get("zero"):
        tables = rec["values_zero"]
        if cmd in state["cleared"]:
            tables = {g: dict.fromkeys(v, 0) for g, v in tables.items()}
        return filtered(tables, params.get("mask"))
    return {} if cmd in state["cleared"] else filtered(rec["values"], params.get("mask"))


def plugin_counters(cmd: str, params: dict[str, Any]) -> dict[str, Any]:
    """A plugin counter request: method-not-found where the server lacks it (the recorded `unsupported`),
    'no valid plugin' where the plugin is not loaded in the namespace or client, else the recorded tables or
    the recorded error."""
    name, scope, rec = PLUGIN_CMDS[cmd]
    if "unsupported" in rec:
        return {"error": NOT_FOUND}
    ns = find_ns(params)
    vport = (params.get("tun") or {}).get("vport")
    if ns is None:
        unknown = "e3_client_counter_unknown_tunnel" if scope == "client" else "e3_ns_counter_unknown_tunnel"
        return {"error": recorded_error(unknown, {"vport": vport})}
    if scope == "client" and tuple(params.get("mac") or ()) not in ns["macs"]:
        return {"error": recorded_error("e3_client_counter_unknown_mac", {"mac": params.get("mac")})}
    if SERVER_NAME[name] not in state["plugins"]:
        text = (NO_PLUGIN_CLIENT if scope == "client" else NO_PLUGIN_TUNNEL).format(plugin=SERVER_NAME[name])
        return {"error": {"code": CODE_INVALID, "message": text}}
    if "error" in rec:
        return {"error": {"code": rec["error"]["code"], "message": rec["error"]["text"]}}
    return {"result": counters(rec, params, cmd)}


def run(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """{"result": ...} or {"error": ...} for one authenticated request."""
    if method == "ping":
        return {"result": PING}
    if method == "ctx_iter":
        out = iterate(params, "ctx", [wire_key(n) for n in state["ns"]])
        return out if "error" in out else {"result": out}
    if method == "ctx_client_iter":
        ns = find_ns(params)
        if ns is None:
            vport = (params.get("tun") or {}).get("vport")
            return {"error": recorded_error("e3_ctx_client_iter_unknown_tunnel", {"vport": vport})}
        out = iterate(params, ns["key"], [list(m) for m in ns["macs"]])
        return out if "error" in out else {"result": out}
    if method == "ctx_get_info":
        found = [(t, find_ns({"tun": t})) for t in params.get("tunnels") or []]
        unknown = next((t for t, n in found if n is None), None)
        if unknown is not None and GET_INFO_FAILS:      # one unknown tunnel fails the whole request
            err = recorded_error("e3_ctx_get_info_unknown_tunnel", {"vport": unknown.get("vport")})
            return {"error": err}
        return {"result": [{"vport": n["key"][0], "tci": list(n["key"][1]), "tpid": list(n["key"][2]),
                            "active_clients": len(n["macs"]),
                            "plug_names": list(n.get("plugs", state["plugins"]))}
                           for _, n in found if n]}
    if method == "ctx_client_get_info":
        ns = find_ns(params)
        if ns is None:
            vport = (params.get("tun") or {}).get("vport")
            return {"error": recorded_error("e3_ctx_client_get_info_unknown_tunnel", {"vport": vport})}
        held = [m for m in params.get("macs") or [] if tuple(m) in ns["macs"]]
        missing = next((m for m in params.get("macs") or [] if tuple(m) not in ns["macs"]), None)
        if missing is not None and CLIENT_INFO_REFUSES:
            return {"error": recorded_error("e3_ctx_client_get_info_unknown_mac", {"mac": missing})}
        infos = []
        for mac in held:
            info = copy.deepcopy(CLIENT_INFO)
            # [U:emu-client-info-synth] the recorded record with an ipv4 made from the MAC
            info.update(mac=list(mac), ipv4=[1, 1, mac[4], mac[5]], plug_names=list(state["plugins"]))
            infos.append(info)
        return {"result": infos}
    if method in WRITES:
        return write(method, params)
    if method == "ctx_cnt":
        return {"result": counters(CNT["ctx"], params, "ctx_cnt")}
    if method in PLUGIN_CMDS:
        return plugin_counters(method, params)
    return {"error": NOT_FOUND}


def handle(req: dict[str, Any]) -> dict[str, Any]:
    """The reply to one decoded request."""
    method, params, rid = req.get("method"), req.get("params") or {}, req.get("id")
    if method == "api_sync_v2":
        api = (params.get("name"), params.get("major"), params.get("minor"))
        if api not in ACCEPTED_API:
            return reply(rid, error={"code": MISMATCH_CODE, "message": MISMATCH_FMT.format(
                name=api[0], major=api[1], minor=api[2])})
        return reply(rid, result={"api_h": state["api_h"]})
    if method == "get_version":
        return reply(rid, result=VERSION)
    known = method in {"ping", "ctx_iter", "ctx_client_iter", "ctx_get_info", "ctx_client_get_info",
                       "ctx_cnt"} or method in PLUGIN_CMDS or method in WRITES
    if known and params.get("api_h") != state["api_h"]:
        return reply(rid, error=BAD_API_H)
    out = run(method, params)
    return reply(rid, error=out["error"]) if "error" in out else reply(rid, result=out["result"])


def main() -> None:
    """Bind, print the ready line and serve until terminated."""
    ap = argparse.ArgumentParser()
    ap.add_argument("port", type=int, help="0 binds any free port; the ready line reports the bound one")
    ap.add_argument("--hang", action="append", default=[], metavar="METHOD")
    ap.add_argument("--fail", action="append", default=[], metavar="METHOD=N")
    ap.add_argument("--huge-plain", action="append", default=[], metavar="METHOD")
    ap.add_argument("--huge-zip", action="append", default=[], metavar="METHOD")
    ap.add_argument("--foreign-ns", type=int, default=0, metavar="K")
    ap.add_argument("--clients", type=int, default=0, metavar="C")
    ap.add_argument("--plugins", default="arp,icmp")
    ap.add_argument("--interrupt-iter", type=int, default=0, metavar="N")
    ap.add_argument("--interrupt-limit", type=int, default=None, metavar="K")
    ap.add_argument("--repeat-iter", type=int, default=0, metavar="N")
    ap.add_argument("--log", default=None, metavar="PATH")
    a = ap.parse_args()
    opts.interrupt_iter, opts.interrupt_limit = a.interrupt_iter, a.interrupt_limit
    opts.repeat_iter = a.repeat_iter
    state["plugins"] = [p for p in a.plugins.split(",") if p]
    seed(a.foreign_ns, a.clients)
    fail_nth = {m: int(n) for m, _, n in (f.partition("=") for f in a.fail)}
    seen: dict[str, int] = {}
    log = open(a.log, "a", buffering=1) if a.log else None      # noqa: SIM115  # open for the process's life
    signal.signal(signal.SIGUSR2, on_restart)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    ctx = zmq.Context()
    sock = ctx.socket(zmq.ROUTER)
    sock.setsockopt(zmq.LINGER, 0)
    host = "tcp://127.0.0.1"
    port = a.port or sock.bind_to_random_port(host)
    if a.port:
        sock.bind(f"{host}:{port}")
    print(f"[fake-emu] ready port={port}", flush=True)
    while True:
        if not sock.poll(50):
            continue
        ident, _empty, raw = sock.recv_multipart()
        if state["restart"]:      # [U:emu-restart-model] a SIGUSR2 restart: new api_h, empty state
            state["restart"] = False
            state.update(api_h=secrets.token_urlsafe(8), ns=[], epoch=0, cursors={}, cleared=set(),
                         def_ns_plugs={})
        req = json.loads(raw)
        method = req.get("method")
        if log:
            log.write(json.dumps({"method": method, "params": {
                k: v for k, v in (req.get("params") or {}).items() if k != "api_h"}}) + "\n")
        if method == "shutdown":        # a client must never send it (ruling R12): the fixture fails the test
            print("[fake-emu] FORBIDDEN shutdown", flush=True)
            continue
        if method in a.hang:
            continue
        seen[method] = seen.get(method, 0) + 1
        if fail_nth.get(method) == seen[method]:
            err = {"code": CODE_INVALID, "message": " injected failure "}  # [U:emu-hook-injected-failure]
            out = json.dumps(reply(req.get("id"), error=err)).encode()
        elif method in a.huge_plain:
            out = json.dumps(reply(req.get("id"), result="x" * HUGE_BYTES)).encode()
        elif method in a.huge_zip:
            out = struct.pack(">II", MAGIC, HUGE_BYTES) + zlib.compress(b"x")
        else:
            out = json.dumps(handle(req)).encode()
        sock.send_multipart([ident, b"", out])


if __name__ == "__main__":
    main()
