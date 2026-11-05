"""Record the P7 EMU wire shapes from the jammy guest's standalone trex-emu (127.0.0.1:<EMU port>).
Usage: uv run --project backend python -I -B vm/record_p7.py <out_dir>
Stops TRex and any trex-emu (vm/mode.sh stop), starts EMU alone (vm/emu.sh start: `trex-emu -p 4510`, no -d,
R5), and in a `finally` stops it again. Every request and reply goes through RawRpc and is logged raw (the
request as sent, the decoded reply), so rpc.jsonl is the wire, not the vendored client's view of it.
Steps: the handshake and version, the error texts (negatives.json, summary.json "drift"), the fixture
profile from profile_wire.json, the listings and info calls, ctx_cnt and every plugin counter (meta and
values; counters.json), an interrupted iteration with two RawRpc clients, the E3 unknown-key and duplicate
steps (summary.json "e3", the error ones also in negatives.json; a request the server never answers is logged
with resp null and the guest's panic text kept), the per-namespace removal and timings.
Fixture files are written only after every step has run; a UTC-stamped copy of each goes to /var/tmp
(canon:OUT-1)."""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
sys.path.insert(0, str(HERE))
import guestenv

EMU_PORT = guestenv.emu_port()  # refuses before connecting if the guest is not selected
from trex_web_gui.monitor.wire import inflate
from trex_web_gui.trexapi.errors import ServerUnreachable, TrexApiError
from trex_web_gui.trexapi.rawrpc import RPC_REPLY_MAX_BYTES, RawRpc, RawRpcError

REPO = HERE.parent
PLUGINS = REPO / "backend" / "trex_web_gui" / "_vendor" / "trex" / "emu" / "emu_plugins"
EMU_API = ("EMU", 1, 1)
# loading it into a scratch namespace left ctx_remove unanswered: recorded last, see record_ipfix
RISKY_PLUGIN = "ipfix"
SERVER_PLUGIN_NAMES = {"transporte": "transe"}  # the server's name where the vendored file name differs
out = Path(sys.argv[1])
t0 = time.time()
log: list[dict] = []
timings: list[dict] = []
counter = [0]


class Tap:
    """A REQ socket stand-in that logs each request and reply as RawRpc sends and receives them."""

    def __init__(self, sock, name: str) -> None:
        """Wrap `sock`; rows go to the module's `log` under the client `name`."""
        self._sock, self._name, self._row = sock, name, None

    def _log_unanswered(self) -> None:
        """Log a remembered request that never got a reply as `resp: None, secs: None`."""
        if self._row is not None:
            self._row["resp"], self._row["secs"] = None, None
            log.append(self._row)
            self._row = None

    def send(self, raw: bytes) -> None:
        """Send and remember the request; a previous request still without a reply is logged unanswered."""
        self._log_unanswered()
        self._row = {"t": round(time.time() - t0, 3), "client": self._name, "req": json.loads(raw)}
        self._start = time.monotonic()
        self._sock.send(raw)

    def recv(self) -> bytes:
        """Receive, log the decoded pair and the elapsed time, and return the bytes unchanged. A reply that
        does not decode is logged as {"undecodable": <its repr, cut at 200 bytes>} and still handed on, so
        RawRpc's own checks refuse it as they would without the tap."""
        reply = self._sock.recv()
        row = self._row
        try:
            row["resp"] = json.loads(inflate(reply, RPC_REPLY_MAX_BYTES))
        except (ValueError, TrexApiError):
            row["resp"] = {"undecodable": repr(reply[:200])}
        row["secs"] = round(time.monotonic() - self._start, 4)
        log.append(row)
        self._row = None
        return reply

    def close(self, linger: int = 0) -> None:
        """Log a request without a reply as unanswered, then close the wrapped socket."""
        self._log_unanswered()
        self._sock.close(linger)

    def __getattr__(self, attr):
        """Everything else is the wrapped socket's."""
        return getattr(self._sock, attr)


class Rpc(RawRpc):
    """RawRpc whose socket is always tapped."""

    def __init__(self, name: str) -> None:
        """Connect to the guest's EMU as client `name`."""
        self._name = name
        super().__init__("127.0.0.1", EMU_PORT, 10.0)

    def _open(self) -> None:
        """Open as RawRpc does, then wrap the socket in a Tap."""
        super()._open()
        self._sock = Tap(self._sock, self._name)

    def last(self) -> dict:
        """The last logged row of this client."""
        return next(r for r in reversed(log) if r["client"] == self._name)


def sh(*cmd: str) -> None:
    """Run a repo script in the foreground; a failure aborts the recording."""
    print("[sh]", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=REPO, check=True)


def err_of(exc: RawRpcError) -> dict:
    """{code, text} of a RawRpcError."""
    return {"code": exc.code, "text": str(exc)}


def ask(rpc: RawRpc, method: str, params: dict | None = None, *, name: str | None = None):
    """One call, timed into `timings`; (result, None) or (None, {code, text}) for an error reply. A call the
    server never answers gets its row too ({"unanswered": text}), and the ServerUnreachable propagates."""
    start = time.monotonic()
    try:
        res, e = rpc.call(method, params), None
    except RawRpcError as exc:
        res, e = None, err_of(exc)
    except ServerUnreachable as exc:
        timings.append({"call": name or method, "secs": round(time.monotonic() - start, 4),
                        "error": {"unanswered": str(exc)}})
        raise
    timings.append({"call": name or method, "secs": round(time.monotonic() - start, 4), "error": e})
    return res, e


def must(rpc: RawRpc, method: str, params: dict | None = None, *, name: str | None = None):
    """The result of a call that has to succeed."""
    res, e = ask(rpc, method, params, name=name)
    if e:
        raise RuntimeError(f"{method} failed: {e}")
    return res


def fresh(name: str, handshake: bool = True) -> Rpc:
    """A new client; with `handshake` it has done api_sync_v2 for EMU 1.1."""
    rpc = Rpc(name)
    if handshake:
        rpc.api_sync(*EMU_API)
    return rpc


def tun(vport: int) -> dict:
    """The tunnel key of the namespace on `vport` (no VLAN)."""
    return {"tun": {"vport": vport, "tci": [0, 0], "tpid": [0, 0]}}


def cnt_params(meta: bool, zero: bool, mask=None, extra: dict | None = None) -> dict:
    """The params the vendored DataCounter sends for a counters RPC."""
    return {"meta": meta, "zero": zero, "mask": mask, "clear": False, **(extra or {})}


def plugin_commands() -> dict[str, dict[str, str]]:
    """{plugin: {"ns": cmd, "client": cmd}} read from the vendored plugin sources (as text, not imported)."""
    found: dict[str, dict[str, str]] = {}
    for path in sorted(PLUGINS.glob("emu_plugin_*.py")):
        text = path.read_text()
        for scope, key in (("ns", "ns_cnt_rpc_cmd"), ("client", "client_cnt_rpc_cmd")):
            m = re.search(rf"{key}\s*=\s*'([a-z0-9_]+)'", text)
            if m:
                found.setdefault(path.stem[len("emu_plugin_") :], {})[scope] = m.group(1)
    return found


def clear_server(rpc: RawRpc) -> None:
    """Remove every namespace as R0.4 says: per namespace walk, remove its clients, then the namespace."""
    res = must(rpc, "ctx_iter", {"count": 255, "reset": True}, name="clear_ctx_iter")
    for ns in res["data"] or []:
        key = {"tun": ns}
        macs = must(rpc, "ctx_client_iter", {"count": 255, "reset": True, **key}, name="clear_client_iter")[
            "data"
        ]
        if macs:
            must(rpc, "ctx_client_remove", {"macs": macs, **key}, name="clear_client_remove")
        must(rpc, "ctx_remove", {"tunnels": [ns]}, name="clear_ctx_remove")


negatives: dict[str, dict] = {}
summary: dict = {}
counters: dict = {"plugins": {}}


def pair(row: dict) -> dict:
    """{req, resp} of a logged row."""
    return {"req": row["req"], "resp": row["resp"]}


def record_handshake_and_errors() -> None:
    """Handshake, version, the negative replies and the drifted methods."""
    summary["handshake"] = {}
    for key, api in (("EMU_1_1", ("EMU", 1, 1)), ("EMU_1_0", ("EMU", 1, 0))):
        rpc = fresh("hs", handshake=False)
        res, e = ask(
            rpc, "api_sync_v2", {"name": api[0], "major": api[1], "minor": api[2]}, name=f"api_sync_v2 {key}"
        )
        summary["handshake"][key] = {"ok": e is None, "error": e, "has_api_h": bool(res and res.get("api_h"))}
        rpc.close()
    rpc = fresh("hs", handshake=False)
    first = must(rpc, "api_sync_v2", {"name": "EMU", "major": 1, "minor": 1})["api_h"]
    again = must(rpc, "api_sync_v2", {"name": "EMU", "major": 1, "minor": 1})["api_h"]
    rpc.close()
    other = fresh("hs")
    summary["handshake"]["api_h_stable_per_server"] = first == again == other._api_h
    summary["version"] = must(other, "get_version")
    summary["ping"] = must(other, "ping")
    other.close()

    for name, api in (("api_sync_v2_major2", ("EMU", 2, 0)), ("api_sync_v2_stl", ("STL", 5, 1))):
        rpc = fresh("neg", handshake=False)
        _, e = ask(rpc, "api_sync_v2", {"name": api[0], "major": api[1], "minor": api[2]}, name=name)
        assert e, name
        negatives[name] = pair(rpc.last())
        rpc.close()
    rpc = fresh("neg", handshake=False)
    summary["get_version_without_api_h"] = must(rpc, "get_version")
    _, e = ask(rpc, "ping", name="ping_without_api_h")
    assert e
    negatives["ping_without_api_h"] = pair(rpc.last())
    rpc._api_h = "bogus"
    _, e = ask(rpc, "ping", name="ping_bogus_api_h")
    assert e
    negatives["ping_bogus_api_h"] = pair(rpc.last())
    rpc.close()

    rpc = fresh("neg")
    summary["drift"] = {}
    for method in ("ctx_stop_autotriggers", "ctx_resource_monitor_get", "ipfix_ns_cnt", "get_supported_cmds"):
        _, e = ask(
            rpc,
            method,
            {} if method != "ipfix_ns_cnt" else cnt_params(True, False, None, tun(0)),
            name=method,
        )
        assert e and e["code"] == -32601, (method, e)
        negatives[f"{method}_not_found"] = pair(rpc.last())
        if method != "get_supported_cmds":
            summary["drift"][method] = e
    rpc.close()


def record_load(rpc: RawRpc) -> list[dict]:
    """Load the fixture profile from profile_wire.json; returns its request list."""
    clear_server(rpc)
    reqs = json.loads((REPO / "tests" / "fixtures" / "p7" / "profile_wire.json").read_text())["requests"]
    for r in reqs:
        must(rpc, r["method"], r["params"], name=f"load {r['method']}")
    return reqs


def record_reads(rpc: RawRpc) -> None:
    """Listings, info calls, ctx_cnt and the plugin counters against the loaded profile."""
    ns_list = must(rpc, "ctx_iter", {"count": 255, "reset": True})
    assert len(ns_list["data"]) == 2, ns_list
    must(rpc, "ctx_iter", {"count": 1, "reset": True}, name="ctx_iter count 1")
    must(rpc, "ctx_iter", {"count": 1, "reset": False}, name="ctx_iter count 1 continued")
    must(rpc, "ctx_iter", {"count": 1, "reset": False}, name="ctx_iter count 1 past the end")
    must(rpc, "ctx_get_info", {"tunnels": ns_list["data"]})
    for ns in ns_list["data"]:
        key = {"tun": ns}
        macs = must(rpc, "ctx_client_iter", {"count": 255, "reset": True, **key})["data"]
        assert len(macs) == 3, macs
        must(rpc, "ctx_client_iter", {"count": 2, "reset": True, **key}, name="ctx_client_iter count 2")
        must(
            rpc,
            "ctx_client_iter",
            {"count": 2, "reset": False, **key},
            name="ctx_client_iter count 2 continued",
        )
        must(rpc, "ctx_client_get_info", {"macs": macs, **key})
    ctx = {
        "meta": must(rpc, "ctx_cnt", cnt_params(True, False)),
        "values": must(rpc, "ctx_cnt", cnt_params(False, False)),
        "values_zero": must(rpc, "ctx_cnt", cnt_params(False, True)),
        "masked": must(rpc, "ctx_cnt", cnt_params(False, True, ["ctx"]), name="ctx_cnt mask ctx"),
    }
    _, e = ask(rpc, "ctx_cnt", cnt_params(False, True, ["no_such_table"]), name="ctx_cnt mask unknown")
    ctx["unknown_mask_error"] = e
    counters["ctx"] = ctx
    _, e = ask(rpc, "igmp_ns_cnt", cnt_params(True, False, None, tun(0)), name="igmp_ns_cnt not loaded")
    assert e and e["code"] == -32600, e
    negatives["plugin_counters_not_loaded"] = pair(rpc.last())
    for index, (plugin, cmds) in enumerate(plugin_commands().items()):
        if plugin != RISKY_PLUGIN:
            counters["plugins"][plugin] = record_plugin(rpc, plugin, cmds, 100 + index)


def record_plugin(rpc: RawRpc, plugin: str, cmds: dict[str, str], vport: int) -> dict:
    """Counter meta and values of one plugin, loaded with an empty config into a scratch namespace on `vport`.

    A scope whose method the server lacks is `unsupported`; a load or query the server refuses is kept as
    `load_error` / `error` beside whatever else was recorded."""
    print(f"[plugin] {plugin}", flush=True)
    server_name = SERVER_PLUGIN_NAMES.get(plugin, plugin)
    plugs = {server_name: {}}
    mac = [0, 0, 0, 112, 9, vport - 99]
    key = tun(vport)
    ns_fields = {
        "vport": vport,
        "tci": [0, 0],
        "tpid": [0, 0],
        "plugs": plugs if "ns" in cmds else {},
        "def_c_plugs": plugs if "client" in cmds else {},
    }
    entry: dict = {"server_name": server_name}
    _, e = ask(rpc, "ctx_add", {"tunnels": [ns_fields]}, name=f"scratch ctx_add {plugin}")
    if e:
        entry["load_error"] = e
    else:
        if "client" in cmds:
            ask(
                rpc,
                "ctx_client_set_def_plugins",
                {"def_plugs": plugs, **key},
                name=f"scratch set_def_plugins {plugin}",
            )
        _, e = ask(
            rpc,
            "ctx_client_add",
            {"clients": [{"mac": mac, "ipv4": [1, 1, 9, vport - 99], "ipv4_dg": [1, 1, 9, 1]}], **key},
            name=f"scratch ctx_client_add {plugin}",
        )
        if e:
            entry["load_error"] = e
    for scope, cmd in cmds.items():
        extra = key if scope == "ns" else {**key, "mac": mac}
        res, e = ask(rpc, cmd, cnt_params(True, False, None, extra), name=f"{cmd} meta")
        item: dict = {"cmd": cmd}
        if e is None:
            item["meta"] = res
            item["values"] = must(rpc, cmd, cnt_params(False, False, None, extra), name=f"{cmd} values")
            item["values_zero"] = must(
                rpc, cmd, cnt_params(False, True, None, extra), name=f"{cmd} zero values"
            )
        elif e["code"] == -32601:
            item["unsupported"] = e["text"]
        else:
            item["error"] = e
        entry[scope] = item
    counters["plugins"][plugin] = entry  # kept even if the removal below takes the server down
    clear_scratch(rpc, key, vport)
    return entry


IPFIX_CONDITION = "ipfix at namespace level only, empty {} config, standalone trex-emu v3.08"
E3_CONDITION = (
    "standalone trex-emu v3.08 (vm/emu.sh, -p 4510, no -d), empty server, namespace 213 with arp at "
    "namespace level, client [0, 0, 0, 112, 99, 1] with no plugins"
)


def panic_block() -> str:
    """The guest's trex-emu process line (or that it is gone) and the whole panic text of its log."""
    diag = subprocess.run(
        [
            str(HERE / "ssh.sh"),
            "pgrep -x trex-emu || echo 'trex-emu is gone'; sed -n '/^panic/,$p' /tmp/trex-emu.log",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    return diag.stdout


def record_ipfix(rpc: RawRpc) -> None:
    """The ipfix plugin last, on an empty server: load it as the others are loaded, read its counters, then
    remove the namespace. The run that found this saw `ctx_remove` go unanswered, so what the server does is
    recorded with the guest's process list and panic text; the caller's `finally` stops EMU."""
    cmds = plugin_commands()[RISKY_PLUGIN]
    try:
        counters["plugins"][RISKY_PLUGIN] = record_plugin(
            rpc, RISKY_PLUGIN, cmds, 100 + list(plugin_commands()).index(RISKY_PLUGIN)
        )
        summary["ipfix_scratch_removal"] = "answered"
    except TrexApiError as exc:
        summary["ipfix_scratch_removal"] = {
            "error": f"{type(exc).__name__}: {exc}",
            "guest": panic_block(),
            "condition": IPFIX_CONDITION,
        }
        counters["plugins"].setdefault(RISKY_PLUGIN, {"not_recorded": "see summary.ipfix_scratch_removal"})


def clear_scratch(rpc: RawRpc, key: dict, vport: int) -> None:
    """Remove scratch namespace `vport` and its clients; a refusal is tolerated (it may not exist)."""
    macs = ask(rpc, "ctx_client_iter", {"count": 255, "reset": True, **key}, name="scratch client_iter")[0]
    if macs and macs["data"]:
        ask(rpc, "ctx_client_remove", {"macs": macs["data"], **key}, name="scratch ctx_client_remove")
    ask(rpc, "ctx_remove", {"tunnels": [key["tun"]]}, name="scratch ctx_remove")


def outcome(rpc: RawRpc, method: str, params: dict, *, name: str) -> dict:
    """A call whose error reply is data: {"result": ...} or {"error": {code, text}}."""
    res, e = ask(rpc, method, params, name=name)
    return {"error": e} if e else {"result": res}


M1, M2, M3 = ([0, 0, 0, 112, 99, n] for n in (1, 2, 3))


def e3_ns(vport: int, plugs: dict | None = None) -> dict:
    """A ctx_add tunnel entry on `vport` with `plugs` at namespace level and no default client plugins."""
    return {"vport": vport, "tci": [0, 0], "tpid": [0, 0], "plugs": plugs or {}, "def_c_plugs": {}}


def e3_client(mac: list[int]) -> dict:
    """The client body of an E3 add: `mac`, with its address inside 1.1.213.0."""
    return {"mac": mac, "ipv4": [1, 1, 213, mac[5]], "ipv4_dg": [1, 1, 213, 254]}


def e3_steps() -> list[tuple[str, str, dict, bool]]:
    """The 29 named E3 steps as (name, method, params, must): `must` marks the three setup steps."""
    walk = {"count": 255, "reset": True}
    t213, t299 = tun(213), tun(299)
    arp = {"arp": {}}
    return [
        ("e3_setup_ctx_add", "ctx_add", {"tunnels": [e3_ns(213, arp)]}, True),
        ("e3_setup_client_add", "ctx_client_add", {"clients": [e3_client(M1)], **t213}, True),
        ("e3_ctx_add_duplicate", "ctx_add", {"tunnels": [e3_ns(213, arp)]}, False),
        ("e3_ctx_add_duplicate_in_request", "ctx_add", {"tunnels": [e3_ns(214), e3_ns(214)]}, False),
        ("e3_ctx_iter_after_duplicate_in_request", "ctx_iter", walk, False),
        ("e3_ctx_add_new_and_existing", "ctx_add", {"tunnels": [e3_ns(215), e3_ns(213, arp)]}, False),
        ("e3_ctx_iter_after_new_and_existing", "ctx_iter", walk, False),
        ("e3_ctx_client_add_duplicate", "ctx_client_add", {"clients": [e3_client(M1)], **t213}, False),
        (
            "e3_ctx_client_add_duplicate_in_request",
            "ctx_client_add",
            {"clients": [e3_client(M3), e3_client(M3)], **t213},
            False,
        ),
        ("e3_ctx_client_iter_after_duplicate_in_request", "ctx_client_iter", {**walk, **t213}, False),
        ("e3_ctx_client_add_unknown_tunnel", "ctx_client_add", {"clients": [e3_client(M2)], **t299}, False),
        (
            "e3_ctx_client_set_def_plugins_unknown_tunnel",
            "ctx_client_set_def_plugins",
            {"def_plugs": {}, **t299},
            False,
        ),
        ("e3_ctx_client_remove_unknown_mac", "ctx_client_remove", {"macs": [M2], **t213}, False),
        ("e3_ctx_client_remove_unknown_tunnel", "ctx_client_remove", {"macs": [M1], **t299}, False),
        ("e3_ctx_client_iter_unknown_tunnel", "ctx_client_iter", {**walk, **t299}, False),
        ("e3_ctx_get_info_unknown_tunnel", "ctx_get_info", {"tunnels": [t299["tun"]]}, False),
        (
            "e3_ctx_get_info_known_and_unknown",
            "ctx_get_info",
            {"tunnels": [t213["tun"], t299["tun"]]},
            False,
        ),
        ("e3_ctx_client_get_info_unknown_tunnel", "ctx_client_get_info", {"macs": [M1], **t299}, False),
        ("e3_ctx_client_get_info_unknown_mac", "ctx_client_get_info", {"macs": [M2], **t213}, False),
        ("e3_ns_counter_unknown_tunnel", "arp_ns_cnt", cnt_params(True, False, None, t299), False),
        (
            "e3_client_counter_unknown_mac",
            "dhcp_client_cnt",
            cnt_params(True, False, None, {**t213, "mac": M2}),
            False,
        ),
        (
            "e3_client_counter_unknown_tunnel",
            "dhcp_client_cnt",
            cnt_params(True, False, None, {**t299, "mac": M1}),
            False,
        ),
        (
            "e3_client_counter_plugin_not_loaded",
            "dhcp_client_cnt",
            cnt_params(True, False, None, {**t213, "mac": M1}),
            False,
        ),
        ("e3_ctx_remove_unknown_tunnel", "ctx_remove", {"tunnels": [t299["tun"]]}, False),
        ("e3_setup_ctx_add_empty", "ctx_add", {"tunnels": [e3_ns(216)]}, True),
        (
            "e3_ctx_remove_known_and_unknown",
            "ctx_remove",
            {"tunnels": [tun(216)["tun"], t299["tun"]]},
            False,
        ),
        ("e3_ctx_iter_after_remove_known_and_unknown", "ctx_iter", walk, False),
        ("e3_ctx_remove_with_clients", "ctx_remove", {"tunnels": [t213["tun"]]}, False),
        ("e3_ctx_iter_after_remove_with_clients", "ctx_iter", walk, False),
    ]


def e3_run(rpc: Rpc, name: str, method: str, params: dict, must_work: bool) -> dict:
    """One E3 step's outcome: {"result"}, {"error"} or {"unanswered", "guest"}; a `must_work` step that gets
    an error reply raises. An error reply is also kept in negatives under `name`."""
    try:
        if must_work:
            return {"result": must(rpc, method, params, name=name)}
        out = outcome(rpc, method, params, name=name)
    except ServerUnreachable as exc:
        return {"unanswered": str(exc), "guest": panic_block()}
    if "error" in out:
        negatives[name] = pair(rpc.last())
    return out


def record_e3(rpc: Rpc) -> Rpc:
    """The E3 steps against an empty server (clear_server first and last). A step the server never answers is
    kept as data; EMU is then restarted, a new client opened and the two setup steps re-run (`resetup`).
    Returns the client in use at the end, which after a crash is a new one."""
    clear_server(rpc)
    steps: list[dict] = []
    resetup: list[dict] = []
    summary["e3"] = {"condition": E3_CONDITION, "steps": steps, "resetup": resetup}
    setup = e3_steps()[:2]
    for name, method, params, must_work in e3_steps():
        out = e3_run(rpc, name, method, params, must_work)
        steps.append({"name": name, "method": method, "params": params, "outcome": out})
        if "unanswered" in out:
            rpc.close()
            sh("vm/emu.sh", "stop")
            sh("vm/emu.sh", "start")
            rpc = fresh("e3")
            for s_name, s_method, s_params, _ in setup:
                res = must(rpc, s_method, s_params, name=f"{s_name} after {name}")
                resetup.append(
                    {
                        "after": name,
                        "name": s_name,
                        "method": s_method,
                        "params": s_params,
                        "outcome": {"result": res},
                    }
                )
    clear_server(rpc)
    return rpc


def record_interrupted(rpc: RawRpc) -> None:
    """Two clients: B resets the iteration while A is mid-way (the server keeps no shared cursor), then B
    changes the set A is walking (ctx_add / ctx_client_add) and A's next page shows what the server says."""
    other = fresh("b")
    first = must(rpc, "ctx_iter", {"count": 1, "reset": True}, name="interrupt A first")
    must(other, "ctx_iter", {"count": 1, "reset": True}, name="interrupt B reset")
    after_reset = outcome(rpc, "ctx_iter", {"count": 1, "reset": False}, name="interrupt A after B reset")
    must(rpc, "ctx_iter", {"count": 1, "reset": True}, name="interrupt A restart")
    must(
        other,
        "ctx_add",
        {"tunnels": [{"vport": 50, "tci": [0, 0], "tpid": [0, 0], "plugs": {}, "def_c_plugs": {}}]},
        name="interrupt B ctx_add",
    )
    after_add = outcome(rpc, "ctx_iter", {"count": 1, "reset": False}, name="interrupt A after B ctx_add")
    rest = must(rpc, "ctx_iter", {"count": 255, "reset": True}, name="interrupt A restart all")
    must(rpc, "ctx_iter", {"count": 1, "reset": True}, name="interrupt A restart again")
    must(
        other,
        "ctx_remove",
        {"tunnels": [{"vport": 50, "tci": [0, 0], "tpid": [0, 0]}]},
        name="interrupt B ctx_remove",
    )
    after_remove = outcome(
        rpc, "ctx_iter", {"count": 1, "reset": False}, name="interrupt A after B ctx_remove"
    )
    summary["interrupted_iteration"] = {
        "first": first,
        "after_reset_by_other": after_reset,
        "after_add_by_other": after_add,
        "all_after_add": rest,
        "after_remove_by_other": after_remove,
    }
    key = tun(0)
    first = must(rpc, "ctx_client_iter", {"count": 1, "reset": True, **key}, name="interrupt clients A first")
    must(
        other,
        "ctx_client_add",
        {"clients": [{"mac": [0, 0, 0, 112, 0, 9], "ipv4": [1, 1, 1, 9], "ipv4_dg": [1, 1, 1, 1]}], **key},
        name="interrupt clients B ctx_client_add",
    )
    after_add = outcome(
        rpc, "ctx_client_iter", {"count": 1, "reset": False, **key}, name="interrupt clients A after B add"
    )
    must(rpc, "ctx_client_iter", {"count": 1, "reset": True, **key}, name="interrupt clients A restart")
    must(
        other, "ctx_client_remove", {"macs": [[0, 0, 0, 112, 0, 9]], **key}, name="interrupt clients B remove"
    )
    after_remove = outcome(
        rpc, "ctx_client_iter", {"count": 1, "reset": False, **key}, name="interrupt clients A after B remove"
    )
    summary["interrupted_client_iteration"] = {
        "first": first,
        "after_add_by_other": after_add,
        "after_remove_by_other": after_remove,
    }
    negatives["ctx_iter_interrupted"] = pair(
        next(r for r in log if r["resp"].get("error") and "interupted" in json.dumps(r["resp"]))
    )
    other.close()


def main() -> None:
    """Record every step, then write the fixture files."""
    sh("vm/mode.sh", "stop")
    sh("vm/emu.sh", "start")
    try:
        record_handshake_and_errors()
        rpc = fresh("a")
        record_load(rpc)
        record_reads(rpc)
        record_interrupted(rpc)
        rpc = record_e3(rpc)
        clear_server(rpc)
        empty = must(rpc, "ctx_iter", {"count": 255, "reset": True}, name="ctx_iter after removal")
        assert empty["empty"] is True, empty
        summary["after_removal_listing"] = empty
        record_ipfix(rpc)
        rpc.close()
    finally:
        sh("vm/emu.sh", "stop")
    summary["port_prerequisite"] = (
        "none on the AF_PACKET guest (all four port-0 states answered; spike-0.md Step 5)"
    )
    summary["emu_api"] = list(EMU_API)
    summary["server"] = "standalone trex-emu -p 4510 (no -d), no TRex, jammy guest"
    summary["timings"] = timings
    summary["slowest_call"] = max(timings, key=lambda t: t["secs"])
    payloads = {
        "rpc.jsonl": "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in log),
        "negatives.json": json.dumps(negatives, indent=1) + "\n",
        "counters.json": json.dumps(counters, indent=1) + "\n",
        "summary.json": json.dumps(summary, indent=1) + "\n",
    }
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M%SZ", time.gmtime())
    for name, text in payloads.items():
        (out / name).write_text(text)
        (Path("/var/tmp") / f"p7-{Path(name).stem}-{stamp}{Path(name).suffix}").write_text(text)
    print(
        json.dumps(
            {"rows": len(log), "plugins": len(counters["plugins"]), "slowest": summary["slowest_call"]},
            indent=1,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (TrexApiError, subprocess.CalledProcessError, AssertionError, RuntimeError) as exc:
        print(f"record_p7 failed: {type(exc).__name__}: {exc}", flush=True)
        raise
