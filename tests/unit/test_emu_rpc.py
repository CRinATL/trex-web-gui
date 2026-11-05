"""backend/trex_web_gui/trexapi/emu.py: the allow-list, the no-clear rule and the error texts (no server)."""
import ast
import json
from pathlib import Path
from typing import Any

import pytest
from trex_web_gui.trexapi import emu
from trex_web_gui.trexapi.emu import EmuRpc, translate_emu
from trex_web_gui.trexapi.errors import (
    EmuUnsupported,
    ServerRestarted,
    ServerUnreachable,
    TrexApiError,
    UnsupportedServerVersion,
)
from trex_web_gui.trexapi.rawrpc import RawRpcError

P7 = Path(__file__).resolve().parent.parent / "fixtures" / "p7"
NEGATIVES = json.loads((P7 / "negatives.json").read_text())

# what translate_emu makes of each recorded negative; a new entry in negatives.json fails the coverage test
EXPECTED: dict[str, type[TrexApiError]] = {
    "api_sync_v2_major2": UnsupportedServerVersion,
    "api_sync_v2_stl": UnsupportedServerVersion,
    "ping_without_api_h": ServerRestarted,
    "ping_bogus_api_h": ServerRestarted,
    "ctx_stop_autotriggers_not_found": EmuUnsupported,
    "ctx_resource_monitor_get_not_found": EmuUnsupported,
    "ipfix_ns_cnt_not_found": EmuUnsupported,
    "get_supported_cmds_not_found": EmuUnsupported,
    "plugin_counters_not_loaded": EmuUnsupported,
    "ctx_iter_interrupted": RawRpcError,
}
# the E3 negatives (recorded by vm/record_p7.py) are read from the fixture: a plugin that is not loaded is
# EmuUnsupported, every other -32600 text stays the server's own RawRpcError
EXPECTED.update(
    {
        name: EmuUnsupported if "no valid plugin" in pair["resp"]["error"]["message"] else RawRpcError
        for name, pair in NEGATIVES.items()
        if name.startswith("e3_")
    }
)


class Spy:
    """Stands in for RawRpc: records every call and answers none of them from a network."""

    def __init__(self) -> None:
        """No calls yet."""
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Record the call; get_version answers a well-formed result, anything else an empty object."""
        self.calls.append((method, params))
        return {"version": "v", "build_date": "d", "build_time": "t", "built_by": "b", "mode": "m"} \
            if method == "get_version" else {}

    def api_sync(self, name: str, major: int, minor: int) -> str:
        """Record the handshake as a call."""
        self.calls.append(("api_sync_v2", {"name": name, "major": major, "minor": minor}))
        return "h"

    def close(self) -> None:
        """Nothing to close."""


def spied() -> tuple[EmuRpc, Spy]:
    """An EmuRpc whose RawRpc is a Spy (the real socket it opened is closed first)."""
    rpc = EmuRpc("127.0.0.1", 1, 0.1)
    rpc._rpc.close()
    spy = Spy()
    rpc._rpc = spy      # type: ignore[assignment]
    return rpc, spy


def test_methods_allow_list_refuses_before_io() -> None:
    """A method outside METHODS (a clear-all, a shutdown, a method the server lacks) raises ValueError and
    reaches the wire not at all; the allow-list holds none of the forbidden names."""
    rpc, spy = spied()
    for bad in ("shutdown", "ctx_stop_autotriggers", "ctx_cnt_clear", "api_sync_v2", ""):
        with pytest.raises(ValueError, match="not an EMU method"):
            rpc.call(bad)
    assert spy.calls == []
    assert not {"shutdown", "ctx_stop_autotriggers"} & emu.METHODS


def test_no_request_carries_clear() -> None:
    """A request with clear true is refused before I/O, and none of the requests the module itself builds
    (connect, ping) carries `clear`."""
    rpc, spy = spied()
    with pytest.raises(ValueError, match="clears counters"):
        rpc.call("ping", {"clear": True})
    assert spy.calls == []
    rpc.connect()
    rpc.ping()
    assert [m for m, _ in spy.calls] == ["api_sync_v2", "get_version", "ping"]
    assert all("clear" not in (p or {}) for _, p in spy.calls)


def test_e3_plugin_not_loaded_is_pinned_as_unsupported() -> None:
    """The client-scope 'no valid plugin' error is EmuUnsupported, named here and not only derived above."""
    assert EXPECTED["e3_client_counter_plugin_not_loaded"] is EmuUnsupported


def test_negatives_are_all_covered() -> None:
    """Every recorded negative has an expectation above."""
    assert set(NEGATIVES) == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(NEGATIVES))
def test_translate_emu_texts_from_negatives(name: str) -> None:
    """The server's recorded error, as RawRpc raises it, becomes the typed error, with the server's own words
    where they are kept."""
    err = NEGATIVES[name]["resp"]["error"]
    out = translate_emu(RawRpcError(err["code"], err["message"]), NEGATIVES[name]["req"]["method"])
    assert type(out) is EXPECTED[name]
    if isinstance(out, UnsupportedServerVersion):
        assert out.server_version == "1.1" and "EMU API 1.1" in out.message
    if isinstance(out, EmuUnsupported):
        assert out.method == NEGATIVES[name]["req"]["method"]
    if isinstance(out, ServerRestarted):
        assert "EMU server restarted" in out.message
    if isinstance(out, RawRpcError):
        assert out.code == err["code"] and out.message == err["message"]


def test_translate_emu_other_exceptions() -> None:
    """A TrexApiError that is not a server error passes unchanged; a foreign exception is unreachable."""
    gone = ServerUnreachable("no answer")
    assert translate_emu(gone, "ping") is gone
    assert isinstance(translate_emu(OSError("boom"), "ping"), ServerUnreachable)


def test_emu_rpc_passes_its_reply_cap_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """EmuRpc builds its RawRpc with reply_max=EMU_REPLY_MAX_BYTES."""
    seen: dict[str, Any] = {}

    class Fake(Spy):
        """A Spy that keeps the keyword arguments it was built with."""

        def __init__(self, host: str, port: int, timeout_s: float, **kw: Any) -> None:
            """Record the arguments."""
            super().__init__()
            seen.update(kw)

    monkeypatch.setattr(emu, "RawRpc", Fake)
    EmuRpc("127.0.0.1", 1, 0.1)
    assert seen == {"reply_max": emu.EMU_REPLY_MAX_BYTES}


def test_methods_equal_the_methods_the_module_sends() -> None:
    """METHODS is exactly the method names emu.py sends: the literals handed to `.call(` or `._walk(`,
    plus the counter methods that `counters` passes by variable (the cursor walk gets its method as a
    literal).
    A new send without an allow-list entry, or an entry nothing sends, fails here
    (the rpc_escape_hatch pattern, spec section 4.1)."""
    tree = ast.parse(Path(emu.__file__).read_text())
    literal: set[str] = set()
    dynamic: set[str] = set()
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("call", "_walk") and node.args):
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    literal.add(first.value)
                else:
                    dynamic.add(fn.name)
    # the three places a method name is a variable: EmuRpc.call (allow-list check), the cursor walk, counters
    assert dynamic == {"call", "_walk_once", "counters"}, dynamic
    assert literal | set(emu.COUNTER_SCOPES) == set(emu.METHODS)


def test_counters_refuse_before_io() -> None:
    """A method that is no counter, a key the scope does not take, or a key it needs: ValueError, no I/O."""
    rpc, spy = spied()
    ns = emu.NsKey(1)
    for args, kw in ((("ping",), {}), (("ctx_cnt", ns), {}), (("arp_ns_cnt",), {}),
                     (("arp_ns_cnt", ns, (0,) * 6), {}), (("dns_c_cnt", ns), {})):
        with pytest.raises(ValueError):
            rpc.counters(*args, **kw)     # type: ignore[arg-type]
    assert spy.calls == []


def test_counters_never_clear_and_send_the_recorded_shape() -> None:
    """The counter request carries meta, zero, mask, `clear: false`, and the tunnel and MAC where the scope
    has them."""
    rpc, spy = spied()
    ns, mac = emu.NsKey(5), (0, 0, 0, 1, 2, 3)
    rpc.counters("ctx_cnt", meta=True)
    rpc.counters("dns_c_cnt", ns, mac, zero=True, mask=["dns"])
    assert spy.calls == [
        ("ctx_cnt", {"meta": True, "zero": False, "mask": None, "clear": False}),
        ("dns_c_cnt", {"meta": False, "zero": True, "mask": ["dns"], "clear": False,
                       "tun": {"vport": 5, "tci": [0, 0], "tpid": [0, 0]}, "mac": [0, 0, 0, 1, 2, 3]})]


def test_method_not_found_is_remembered_but_not_a_missing_plugin() -> None:
    """After -32601 for a counter method the next request for it sends nothing; a 'no valid plugin' -32600
    is per scope, so it is reported each time and the request is sent each time."""
    rpc, spy = spied()
    ns = emu.NsKey(0)
    answers = iter([RawRpcError(-32601, "Method not found"),
                    RawRpcError(-32600, " error there is no valid plugin igmp for this tunnel "),
                    RawRpcError(-32600, " error there is no valid plugin igmp for this tunnel ")])

    def call(method: str, params: dict[str, Any] | None = None) -> Any:
        """Record and raise the next answer."""
        spy.calls.append((method, params))
        raise next(answers)
    spy.call = call     # type: ignore[method-assign]
    for _ in range(2):
        with pytest.raises(EmuUnsupported):
            rpc.counters("ipfix_ns_cnt", ns)
    assert [m for m, _ in spy.calls] == ["ipfix_ns_cnt"]
    for _ in range(2):
        with pytest.raises(EmuUnsupported):
            rpc.counters("igmp_ns_cnt", ns)
    assert [m for m, _ in spy.calls] == ["ipfix_ns_cnt", "igmp_ns_cnt", "igmp_ns_cnt"]


def test_writes_chunk_at_255_and_send_the_recorded_shapes() -> None:
    """600 clients, MACs or namespaces go out as 255, 255 and 90; the shapes are the recorded ones; no write
    carries `clear`."""
    rpc, spy = spied()
    ns = emu.NsKey(3)
    rpc.add_clients(ns.wire(), [{"mac": [0, 0, 0, 0, i // 256, i % 256]} for i in range(600)])
    rpc.remove_clients(ns, [(0, 0, 0, 0, i // 256, i % 256) for i in range(600)])
    rpc.add_namespaces([{"vport": i} for i in range(600)])
    rpc.remove_namespaces([emu.NsKey(i) for i in range(600)])
    sizes = {m: [len(next(iter(p[k] for k in ("clients", "macs", "tunnels") if k in p)))
                 for mm, p in spy.calls if mm == m] for m in
             ("ctx_client_add", "ctx_client_remove", "ctx_add", "ctx_remove")}
    assert all(v == [255, 255, 90] for v in sizes.values()), sizes
    rpc.set_def_ns_plugins({"arp": {}})
    rpc.set_def_client_plugins(ns.wire(), {"icmp": {}})
    assert spy.calls[-2:] == [("ctx_set_def_plugins", {"def_plugs": {"arp": {}}}),
                              ("ctx_client_set_def_plugins", {"def_plugs": {"icmp": {}}, "tun": ns.wire()})]
    assert all("clear" not in (p or {}) for _, p in spy.calls)


def test_rate_pacer_waits_for_the_schedule() -> None:
    """RatePacer asks for the time still owed to keep to the rate; None never waits."""
    now = [10.0]
    pacer = emu.RatePacer(500, lambda: now[0])
    assert pacer.after(255) == pytest.approx(0.51)
    now[0] += 0.51
    assert pacer.after(255) == pytest.approx(0.51)
    now[0] += 5                 # slower than the rate: nothing owed
    assert pacer.after(255) == 0.0
    assert emu.RatePacer(None, lambda: 0.0).after(10**6) == 0.0


def test_ping_times_one_call_on_the_given_clock() -> None:
    """ping() reads `clock` before and after one ping request and returns the difference."""
    rpc, spy = spied()
    readings = iter([10.0, 10.25])
    rpc.clock = lambda: next(readings)
    assert rpc.ping() == 0.25
    assert spy.calls == [("ping", None)]


def test_an_empty_page_ends_the_walk_even_without_stopped_or_empty() -> None:
    """A page with no data ends a cursor walk although it sets neither `stopped` nor `empty`: one request,
    an empty complete listing (no endless re-asking)."""
    rpc, spy = spied()
    pages = iter([{"data": [], "stopped": False, "empty": False}])

    def call(method: str, params: dict[str, Any] | None = None) -> Any:
        """Record and answer the next page; a second request has no page and fails the test."""
        spy.calls.append((method, params))
        return next(pages)
    spy.call = call     # type: ignore[method-assign]
    listing = rpc.list_namespaces()
    assert (listing.items, listing.interrupted) == ([], False)
    assert [m for m, _ in spy.calls] == ["ctx_iter"]
