"""The packet engine (P4b Task 1): the persistent packet worker, its lifecycle rules and its two ops.
Every body runs in one asyncio.run through `with_engine`, which closes the engine in `finally`."""
import ast
import asyncio
import base64
import json
import os
import signal
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from support import ROUTER_ALERT_FRAME
from trex_web_gui.studio.packetdoc import PacketDoc, model_of, parse_doc
from trex_web_gui.trexapi import packet
from trex_web_gui.trexapi.packet import OptionsRefused, PacketEngine, PacketError

PROTOCOL_KEYS = ["fields", "id", "name"]
FIELD_KEYS = ["auto", "bits", "id", "max", "min", "name", "regex", "type", "values_dict"]
LAYER_KEYS = ["fields", "id", "offset", "real_id", "valid_structure"]
FIELD_RESULT_KEYS = ["hvalue", "id", "length", "offset", "options", "value"]   # `options`: IP and TCP options

ETHER_IP = [{"id": "Ether"}, {"id": "IP"}]


def with_engine[T](fn: Callable[[PacketEngine], Awaitable[T]]) -> T:
    """Run `fn` with a fresh PacketEngine in one event loop, and close the engine afterwards."""
    async def go() -> T:
        """Create the engine, await `fn`, close in finally."""
        engine = PacketEngine()
        try:
            return await fn(engine)
        finally:
            await engine.close()
    return asyncio.run(go())


def ip_id(expr: str) -> list[dict[str, Any]]:
    """An Ether/IP packet model whose IP.id is the EXPRESSION `expr`."""
    value = {"vtype": "EXPRESSION", "expr": expr}
    return [{"id": "Ether"}, {"id": "IP", "fields": [{"id": "id", "value": value}]}]


def gone(pid: int) -> bool:
    """True when process `pid` no longer exists or is a zombie."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return True
    return state == "Z"


async def wait_gone(pid: int, within_s: float) -> bool:
    """Poll until `pid` is gone, up to `within_s` seconds."""
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        if gone(pid):
            return True
        await asyncio.sleep(0.05)
    return gone(pid)


def test_definitions_shape_and_recorded_keys() -> None:
    async def body(e: PacketEngine) -> None:
        d = await e.definitions()
        assert len(d["protocols"]) > 300
        assert sorted({k for p in d["protocols"] for k in p}) == PROTOCOL_KEYS
        assert sorted({k for p in d["protocols"] for f in p["fields"] for k in f}) == FIELD_KEYS
        fields = {(p["id"], f["id"]): f for p in d["protocols"] for f in p["fields"]}
        assert fields[("IP", "options")]["type"] == "IP_OPTIONS"
        assert fields[("TCP", "options")]["type"] == "TCP_OPTIONS"
        assert fields[("IP", "flags")]["type"] == "BITMASK" and fields[("IP", "flags")]["bits"]
        assert fields[("Dot1Q", "type")]["type"] == "ENUM"
        assert [i["id"] for i in d["feInstructions"]] == [
            "STLVmFlowVar", "STLVmWrFlowVar", "STLVmWrMaskFlowVar", "STLVmFixIpv4", "STLVmTrimPktSize",
            "STLVmTupleGen", "STLVmFlowVarRepeatableRandom", "STLVmFixChecksumHw", "STLVmFixIcmpv6"]
        assert len(d["feInstructionParameters"]) == 29
        assert [t["id"] for t in d["feTemplates"]] == ["simple_flow_var", "rep_rand_var"]
        assert len(d["etag"]) == 16 and int(d["etag"], 16) >= 0
        assert (await e.definitions())["etag"] == d["etag"]
        assert (await e.definitions()) is d                      # cached per worker start
        assert e.starts == 1
        ids = {p["id"] for p in d["protocols"]}
        assert ids < set(d["layerIds"])
        assert {"ICMPv6EchoRequest", "Dot1AD", "Padding"} <= set(d["layerIds"])
        assert d["layerIds"] == sorted(d["layerIds"])
    with_engine(body)


def test_build_layer_and_field_keys() -> None:
    async def body(e: PacketEngine) -> None:
        raw = {"vtype": "BYTES", "generate": "random_ascii", "size": 4}
        r = await e.build([{"id": "Ether"}, {"id": "Dot1Q"}, {"id": "IP"}, {"id": "TCP"},
                           {"id": "Raw", "fields": [{"id": "load", "value": raw}]}], None)
        assert r["error"] is None and r["field_engine"] is None and r["binary"]
        assert sorted({k for layer in r["data"] for k in layer}) == LAYER_KEYS
        assert sorted({k for layer in r["data"] for f in layer["fields"] for k in f}) == FIELD_RESULT_KEYS
        assert [layer["id"] for layer in r["data"]] == ["Ether", "Dot1Q", "IP", "TCP", "Raw"]
        assert len(base64.b64decode(r["binary"])) == 14 + 4 + 20 + 20 + 4
    with_engine(body)


def flow_fe(lo: str = "16.0.0.1", hi: str = "16.0.0.10") -> dict[str, Any]:
    """The probe's field-engine model: a 4-byte incrementing flow var written into IP.src."""
    return {"instructions": [
        {"id": "STLVmFlowVar", "parameters": {"name": "ip_src", "min_value": lo, "max_value": hi,
                                              "size": "4", "op": "inc"}},
        {"id": "STLVmWrFlowVar", "parameters": {"fv_name": "ip_src", "pkt_offset": "IP.src"}}],
        "global_parameters": {"cache_size": "10"}}


def test_build_with_fe_compiles_the_vm() -> None:
    async def body(e: PacketEngine) -> None:
        r = await e.build(ETHER_IP + [{"id": "UDP"}], flow_fe())
        fe = r["field_engine"]
        assert r["error"] is None and fe is not None and fe["error"] is None
        vm = fe["instructions"]
        assert vm["cache"] == 10
        flow, write = vm["instructions"]
        assert flow["type"] == "flow_var" and write["type"] == "write_flow_var"
        assert (flow["min_value"], flow["max_value"]) == (268435457, 268435466)
        assert write["pkt_offset"] == 26 and "vm_instructions_expressions" not in fe
    with_engine(body)


def test_invalid_fe_is_an_error_not_a_crash() -> None:
    async def body(e: PacketEngine) -> None:
        r = await e.build(ETHER_IP, flow_fe("10", "1"))
        assert r["field_engine"] is not None and "lower than min" in str(r["field_engine"]["error"])
        assert r["binary"]
        pid = e.pid
        assert (await e.build(ETHER_IP, None))["binary"] and e.pid == pid
    with_engine(body)


def test_unknown_instruction_is_an_fe_error() -> None:
    async def body(e: PacketEngine) -> None:
        fe = {"instructions": [{"id": "Nope", "parameters": {}}], "global_parameters": {}}
        r = await e.build(ETHER_IP, fe)
        assert r["error"] is None and r["binary"]
        assert r["field_engine"] is not None and "Nope" in str(r["field_engine"]["error"])
    with_engine(body)


def test_bad_layer_value_is_a_build_error() -> None:
    async def body(e: PacketEngine) -> None:
        r = await e.build([{"id": "Ether"}, {"id": "IP", "fields": [{"id": "ttl", "value": "abc"}]}],
                          flow_fe())
        assert r["error"] and r["binary"] is None and r["data"] == [] and r["field_engine"] is None
        assert (await e.build(ETHER_IP, None))["error"] is None
    with_engine(body)


def test_one_request_at_a_time() -> None:
    async def body(e: PacketEngine) -> None:
        a, b = await asyncio.gather(e.build(ETHER_IP, None), e.build(ETHER_IP + [{"id": "UDP"}], None))
        assert len(base64.b64decode(a["binary"] or "")) == 34
        assert len(base64.b64decode(b["binary"] or "")) == 42
        assert e.starts == 1
    with_engine(body)


@pytest.fixture
def small_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 1 GiB address-space limit for the worker."""
    monkeypatch.setattr(packet, "WORKER_AS_LIMIT", 1024**3)


def test_allocating_expression_restarts_the_worker(small_limit: None) -> None:
    async def body(e: PacketEngine) -> None:
        assert (await e.build(ETHER_IP, None))["binary"]            # the worker fits under the limit
        first = e.pid
        with pytest.raises(PacketError, match="memory") as ei:
            await e.build(ip_id("len(bytearray(2*1024**3))"), None)
        assert ei.value.restarted is True
        assert (await e.build(ETHER_IP, None))["binary"]
        assert e.pid != first and e.starts == 2
    with_engine(body)


def test_hanging_expression_times_out_and_restarts() -> None:
    async def body(e: PacketEngine) -> None:
        assert (await e.build(ETHER_IP, None))["binary"]
        first = e.pid
        with pytest.raises(PacketError, match="did not answer") as ei:
            await e.call("build", {"packet": ip_id("__import__('time').sleep(30)"), "fe": None}, timeout_s=1)
        assert ei.value.restarted is True
        assert (await e.build(ETHER_IP, None))["binary"] and e.pid != first
    with_engine(body)


def test_expression_runs_in_the_worker() -> None:
    async def body(e: PacketEngine) -> None:
        r = await e.build(ip_id("__import__('os').getpid() % 65536"), None)
        ip = next(layer for layer in r["data"] if layer["id"] == "IP")
        ident = next(f for f in ip["fields"] if f["id"] == "id")
        assert e.pid is not None and ident["value"] == e.pid % 65536
        assert os.getpid() % 65536 != e.pid % 65536
    with_engine(body)


def test_worker_exits_on_stdin_eof() -> None:
    async def body(e: PacketEngine) -> None:
        await e.build(ETHER_IP, None)
        proc = e.process
        assert proc is not None and proc.stdin is not None
        proc.stdin.close()
        assert await asyncio.wait_for(proc.wait(), 2) == 0
    with_engine(body)


HELPER = """
import asyncio, os, signal, sys
from trex_web_gui.trexapi.packet import PacketEngine

async def main(pidfile, marker):
    e = PacketEngine()
    expr = f"(open({marker!r}, 'w').close(), __import__('time').sleep(30))"
    value = {"vtype": "EXPRESSION", "expr": expr}
    model = [{"id": "Ether"}, {"id": "IP", "fields": [{"id": "id", "value": value}]}]
    task = asyncio.ensure_future(e.call("build", {"packet": model, "fe": None}, timeout_s=60))
    while not os.path.exists(marker):            # the worker's eval has begun (it wrote the marker)
        await asyncio.sleep(0.02)
    open(pidfile, "w").write(str(e.pid))
    os.kill(os.getpid(), signal.SIGKILL)

asyncio.run(main(sys.argv[1], sys.argv[2]))
"""


def test_worker_dies_with_its_parent(tmp_path: Path) -> None:
    """The worker is inside `eval` (it wrote a marker from there), not at readline, so only the parent-death
    rule can end it (rule b). Not tested: prctl alone, because the 1 s watchdog also ends the orphan and
    telling them apart needs a switch for the watchdog in the worker."""
    pidfile = tmp_path / "pid"
    helper = subprocess.run([sys.executable, "-c", HELPER, str(pidfile), str(tmp_path / "in-eval")],
                            capture_output=True, timeout=60)
    assert helper.returncode == -signal.SIGKILL, helper.stderr.decode()[-500:]
    pid = int(pidfile.read_text())

    async def wait() -> bool:
        """Wait up to 3 s for the orphan to be gone."""
        return await wait_gone(pid, 3)
    try:
        assert asyncio.run(wait())
    finally:
        if not gone(pid):
            os.killpg(pid, signal.SIGKILL)


def test_ready_line_reports_pdeathsig() -> None:
    async def body(e: PacketEngine) -> None:
        await e.build(ETHER_IP, None)
        assert e.ready is not None and e.ready["ready"] is True
        assert e.ready["pdeathsig"] is (sys.platform == "linux")
    with_engine(body)


def test_close_during_a_call() -> None:
    async def body(e: PacketEngine) -> None:
        await e.build(ETHER_IP, None)
        pid = e.pid
        assert pid is not None
        task = asyncio.ensure_future(e.call("build", {"packet": ip_id("__import__('time').sleep(30)"),
                                                      "fe": None}, timeout_s=60))
        await asyncio.sleep(0.5)
        t0 = time.monotonic()
        await e.close()
        assert time.monotonic() - t0 < 1
        with pytest.raises(PacketError, match="closed") as ei:
            await task
        assert ei.value.restarted is False
        assert await wait_gone(pid, 1)
        with pytest.raises(PacketError, match="closed"):
            await e.build(ETHER_IP, None)
        assert e.closed
    with_engine(body)


def test_call_after_close_raises() -> None:
    async def body(e: PacketEngine) -> None:
        await e.build(ETHER_IP, None)
        await e.close()
        starts = e.starts
        for _ in range(2):
            with pytest.raises(PacketError, match="closed"):
                await e.build(ETHER_IP, None)
        assert e.starts == starts and e.pid is None
    with_engine(body)


def test_cancelled_call_kills_the_worker() -> None:
    async def body(e: PacketEngine) -> None:
        await e.build(ETHER_IP, None)
        first = e.pid
        task = asyncio.ensure_future(e.call("build", {"packet": ip_id("__import__('time').sleep(30)"),
                                                      "fe": None}, timeout_s=60))
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        r = await e.build(ETHER_IP + [{"id": "UDP"}], None)
        assert len(base64.b64decode(r["binary"] or "")) == 42 and e.pid != first
    with_engine(body)


def test_queue_wait_is_not_the_call_timeout() -> None:
    async def body(e: PacketEngine) -> None:
        await e.build(ETHER_IP, None)
        first = e.pid
        slow = asyncio.ensure_future(e.call("build", {"packet": ip_id("__import__('time').sleep(3)"),
                                                      "fe": None}, timeout_s=10))
        await asyncio.sleep(0.3)
        t0 = time.monotonic()
        r = await e.call("build", {"packet": ETHER_IP, "fe": None}, timeout_s=1)
        assert r["ok"] is True and time.monotonic() - t0 > 2        # it waited behind the 3 s call
        assert (await slow)["ok"] is True
        assert e.pid == first and e.starts == 1
    with_engine(body)


def test_slow_start_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packet, "PACKET_START_S", 0.01)

    async def body(e: PacketEngine) -> None:
        with pytest.raises(PacketError, match="start") as ei:
            await e.build(ETHER_IP, None)
        assert ei.value.restarted is True
        assert e.starts == 1 and e.pid is None
    with_engine(body)


def test_worker_cwd_is_private() -> None:
    async def body(e: PacketEngine) -> None:
        load = {"vtype": "EXPRESSION", "expr": "__import__('os').getcwd().encode()"}
        r = await e.build([{"id": "Ether"}, {"id": "Raw", "fields": [{"id": "load", "value": load}]}], None)
        cwd = base64.b64decode(r["binary"] or "")[14:].decode()
        assert Path(cwd).parent.resolve() == Path(tempfile.gettempdir()).resolve()
        assert Path(cwd).name.startswith("trex-web-gui-packet-")
        await e.close()
        assert not Path(cwd).exists()                               # removed after the worker exits
    with_engine(body)


def test_build_latency_recorded() -> None:
    async def body(e: PacketEngine) -> None:
        load = {"vtype": "BYTES", "generate": "random_ascii", "size": 64}
        raw = {"id": "Raw", "fields": [{"id": "load", "value": load}]}
        model = [{"id": "Ether"}, {"id": "IP"}, {"id": "UDP"}, raw]
        await e.build(model, None)
        ms = []
        for _ in range(200):
            t0 = time.perf_counter()
            await e.build(model, flow_fe())
            ms.append((time.perf_counter() - t0) * 1000)
        ms.sort()
        p50, p95 = statistics.median(ms), ms[int(len(ms) * 0.95) - 1]
        print(f"packet build over the worker pipe: p50 {p50:.2f} ms, p95 {p95:.2f} ms (target p95 <= 50 ms)")
        assert p95 < 250
    with_engine(body)


SLOW_READY_WORKER = """
import json, sys, time
time.sleep(1)
print(json.dumps({"ready": True, "pdeathsig": False}), flush=True)
sys.stdin.readline()
"""


def test_ready_is_none_until_the_ready_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packet, "_boot", lambda limit, module: SLOW_READY_WORKER)

    async def body(e: PacketEngine) -> None:
        task = asyncio.ensure_future(e.call("build", {"packet": ETHER_IP, "fe": None}, timeout_s=0.5))
        await asyncio.sleep(0.4)
        assert e.pid is not None and e.ready is None        # spawned, no ready line yet
        with pytest.raises(PacketError, match="packet worker"):    # the fake exits without a reply
            await task
        assert e.ready is None                                       # no worker left
    with_engine(body)


def test_close_while_the_worker_is_starting(monkeypatch: pytest.MonkeyPatch) -> None:
    async def body(e: PacketEngine) -> None:
        real = asyncio.create_subprocess_exec
        procs: list[Any] = []

        async def spawn(*a: Any, **k: Any) -> Any:
            """Spawn, then run close() before the engine has seen the new worker."""
            proc = await real(*a, **k)
            procs.append(proc)
            asyncio.ensure_future(e.close())
            await asyncio.sleep(0)
            return proc
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        with pytest.raises(PacketError, match="closed") as ei:
            await e.build(ETHER_IP, None)
        assert ei.value.restarted is False and e.pid is None
        assert await wait_gone(procs[0].pid, 2)
        with pytest.raises(PacketError, match="closed"):
            await e.build(ETHER_IP, None)
    with_engine(body)


FAKE_WORKER = """
import json, sys
print(json.dumps({"ready": True, "pdeathsig": False}), flush=True)
for line in sys.stdin:
    print(json.dumps({"id": 999, "ok": True}), flush=True)
"""


def test_reply_for_another_request_kills_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packet, "_boot", lambda limit, module: FAKE_WORKER)

    async def body(e: PacketEngine) -> None:
        with pytest.raises(PacketError, match="another request") as ei:
            await e.call("build", {"packet": ETHER_IP, "fe": None})
        assert ei.value.restarted is True and e.pid is None
    with_engine(body)


def test_a_busy_engine_refuses_and_kills_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packet, "PACKET_QUEUE_S", 0.3)

    async def body(e: PacketEngine) -> None:
        await e.build(ETHER_IP, None)
        first = e.pid
        slow = asyncio.ensure_future(e.call("build", {"packet": ip_id("__import__('time').sleep(1.5)"),
                                                      "fe": None}, timeout_s=10))
        await asyncio.sleep(0.2)
        with pytest.raises(PacketError, match="busy") as ei:
            await e.build(ETHER_IP, None)
        assert ei.value.restarted is False
        assert (await slow)["ok"] is True and e.pid == first and e.starts == 1
    with_engine(body)


# ---- Task 2: operations on documents, bytes and the Field Engine -------------------------------------

BACKEND = Path(packet.__file__).resolve().parents[2]
TEMPLATES = BACKEND / "trex_web_gui" / "_vendor" / "trex" / "scapy_server" / "templates"
UDP_PORTS = [{"id": "sport", "value": 1025}, {"id": "dport", "value": 12}]
# Frames made once with Scapy (the test process never imports it): hex of the wire bytes.
FRAMES = {
    "icmpv6": "00000001000000000002000086dd6000000000083a40000000000000000000000000000000020000"
              "000000000000000000000000000180007fba00000000",
    "padded": "0000000100000000000200000800450000200001000040113acb10000001300000010401000c000c"
              "cad6787878780000000000000000000000000000",
    "dot1ad": "00000001000000000002000088a800058100000708004500001c0001000040113acf100000013000"
              "00010401000c0008bbcf",
    "ip_options": "00000001000000000002000008004600002300010000401137c61000000130000001010101010401"
                  "000c000bf766616263",
    "tcp_options": "00000001000000000002000008004500003c0001000040063aba1000000130000001001400500000"
                   "000000000000a0022000e7940000020405b40103030a0402080a0000000100000002",
    "dns": "0000000100000000000200000800450000330001000040113ab8100000013000000100350035001f"
           "4812000001000001000000000000016103636f6d0000010001",
    "snmp": "00000001000000000002000008004500003e0001000040113aad100000013000000100a100a1002a"
            "9238302002010104067075626c6963a0130201010201000201003008300606022b060500",
    "flags_pa": "00000001000000000002000008004500002a000140004006facb1000000130000001040100500000"
                "00000000000050182000e30e00006869",
    "flags_sa": "0000000100000000000200000800450000280001000040063ace1000000130000001040100500000"
                "000000000000501220004b800000",
    # The server's IGMP-JOIN template cannot be built by the service (two classes are named IGMP), so its
    # shape is used here as bytes.
    "igmp_join": "01005e000102080027663b748100000a080046000020000140000102fb090ac0fa0ae40001029404"
                 "0000160004fde4000102",
}


def b64(raw: bytes) -> str:
    """Base64 text of `raw`."""
    return base64.b64encode(raw).decode()


def walk(value: Any) -> Any:
    """Yield every node of a JSON value."""
    yield value
    if isinstance(value, dict):
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def udp_doc(ttl: int = 33, dport: int = 12) -> PacketDoc:
    """Ether / IP(ttl) / UDP(sport 1025, dport) / Raw(random_ascii, 16 bytes), no field engine."""
    return parse_doc({"packet": [
        {"id": "Ether"}, {"id": "IP", "fields": [{"id": "ttl", "value": ttl}]},
        {"id": "UDP", "fields": [{"id": "sport", "value": 1025}, {"id": "dport", "value": dport}]},
        {"id": "Raw", "fields": [{"id": "load", "value": {"vtype": "BYTES", "generate": "random_ascii",
                                                          "size": 16}}]}]})


async def rebuilt(e: PacketEngine, doc: PacketDoc) -> bytes:
    """The bytes the service builds from `doc`."""
    packet_model, fe = model_of(doc)
    r = await e.build(packet_model, fe)
    assert r["error"] is None and r["binary"], r["error"]
    return base64.b64decode(r["binary"])


def test_vm_python_is_upstream_to_code() -> None:
    script = (
        "import sys\nsys.path.insert(0, sys.argv[1])\n"
        "from trex_web_gui.trexapi.loader import load\nload()\n"
        "from scapy.all import Ether, IP, UDP\n"
        "from trex.stl.api import STLPktBuilder, STLScVmRaw, STLStream, STLVmFlowVar, STLVmWrFlowVar\n"
        "vm = STLScVmRaw([STLVmFlowVar(name='ip_src', min_value='16.0.0.1', max_value='16.0.0.10', size=4,"
        " op='inc'), STLVmWrFlowVar(fv_name='ip_src', pkt_offset='IP.src')], cache_size=10)\n"
        "code = STLStream(packet=STLPktBuilder(pkt=Ether()/IP()/UDP(), vm=vm)).to_code()\n"
        "lines = code.split('\\n')\n"
        "stop = [i for i, ln in enumerate(lines) if ln.startswith('stream = STLStream(')][0]\n"
        "print('\\n'.join(lines[lines.index('vm = STLVM()'):stop]))\n")
    want = subprocess.run([sys.executable, "-I", "-c", script, str(BACKEND)], capture_output=True, text=True,
                          check=True).stdout.rstrip("\n")

    async def body(e: PacketEngine) -> None:
        got = await e.vm_python([{"id": "Ether"}, {"id": "IP"}, {"id": "UDP"}], flow_fe())
        assert got == want and got.startswith("vm = STLVM()\nvm.var(name='ip_src'")
        assert "stream = STLStream(" not in got and got.endswith("vm.set_cached(10)")
    with_engine(body)


def test_vm_python_keeps_an_explicit_init_value() -> None:
    """Vendor patch 12: Copy as Python names the init_value the editor set."""
    async def body(e: PacketEngine) -> None:
        fe = flow_fe()
        fe["instructions"][0]["parameters"]["init_value"] = "16.0.0.5"
        got = await e.vm_python([{"id": "Ether"}, {"id": "IP"}, {"id": "UDP"}], fe)
        assert "init_value='16.0.0.5'" in got
        plain = await e.vm_python([{"id": "Ether"}, {"id": "IP"}, {"id": "UDP"}], flow_fe())
        assert "init_value='16.0.0.1'" in plain       # the default, min_value, as the library writes it
    with_engine(body)


def test_vm_python_refuses_unsafe_names() -> None:
    fe = {"instructions": [
        {"id": "STLVmFlowVar", "parameters": {"name": "a'b", "min_value": "1", "max_value": "5",
                                              "size": "4", "op": "inc"}},
        {"id": "STLVmWrFlowVar", "parameters": {"fv_name": "a'b", "pkt_offset": "IP.src"}}],
        "global_parameters": {}}

    async def body(e: PacketEngine) -> None:
        with pytest.raises(PacketError, match=r"unsafe for code: vm\.instructions\[0\]\.name") as ei:
            await e.vm_python(ETHER_IP, fe)
        assert ei.value.restarted is False and e.starts == 1
        assert (await e.vm_python(ETHER_IP, flow_fe())).startswith("vm = STLVM()")
    with_engine(body)


def test_payload_classes_ether_ip() -> None:
    async def body(e: PacketEngine) -> None:
        assert await e.payload_classes(ETHER_IP) == ["TCP", "UDP", "ICMP", "Raw"]
    with_engine(body)


def test_param_values_fv_name() -> None:
    async def body(e: PacketEngine) -> None:
        fe = flow_fe()
        assert await e.param_values(ETHER_IP, fe, "fv_name") == {"ip_src": "ip_src"}
        assert "IP.src" in await e.param_values(ETHER_IP, None, "pkt_offset")
    with_engine(body)


def test_doc_from_binary_rebuilds_byte_identical() -> None:
    async def body(e: PacketEngine) -> None:
        listed = await e.templates()
        assert len(listed) == 14
        frames: dict[str, bytes] = {}
        for t in listed:
            if t["id"] == "IGMP-JOIN":
                frames[t["id"]] = bytes.fromhex(FRAMES["igmp_join"])
                continue
            doc = await e.template(t["id"])
            frames[t["id"]] = await rebuilt(e, doc)
        frames.update({k: bytes.fromhex(FRAMES[k]) for k in ("icmpv6", "padded", "dot1ad")})
        assert len(frames["padded"]) == 60
        for name, raw in frames.items():
            res = await e.doc_from_binary(b64(raw), None)
            assert await rebuilt(e, res["doc"]) == raw, name
            if name != "IGMP-JOIN":                       # the service cannot build IGMP: Ether+Raw there
                assert res["warnings"] == [], name
        icmpv6 = await e.doc_from_binary(b64(frames["icmpv6"]), None)
        assert [layer["id"] for layer in icmpv6["doc"]["packet"]] == ["Ether", "IPv6", "ICMPv6EchoRequest"]
        assert icmpv6["warnings"] == []
    with_engine(body)


def test_doc_from_binary_emits_no_code() -> None:
    async def body(e: PacketEngine) -> None:
        for name in ("ip_options", "tcp_options", "dns", "snmp"):
            raw = bytes.fromhex(FRAMES[name])
            res = await e.doc_from_binary(b64(raw), None)
            exprs = [n["expr"] for n in walk(res["doc"]["packet"])
                     if isinstance(n, dict) and n.get("vtype") == "EXPRESSION"]
            for expr in exprs:
                ast.literal_eval(expr)
            assert await rebuilt(e, res["doc"]) == raw, name
            if name in ("ip_options", "tcp_options"):      # opened field by field, options as exact bytes
                assert not exprs and res["warnings"] == [], name
            else:
                assert not exprs and len(res["warnings"]) == 1
                assert "cannot be edited as a field here; from this layer on the packet is raw bytes" \
                    in res["warnings"][0]
        ip = await e.doc_from_binary(b64(bytes.fromhex(FRAMES["ip_options"])), None)
        assert [layer["id"] for layer in ip["doc"]["packet"]] == ["Ether", "IP", "UDP", "Raw"]
        assert field_in(ip["doc"]["packet"][1], "options") == {"vtype": "MACHINE", "base64": b64(b"\1\1\1\1")}
    with_engine(body)


def test_doc_from_binary_with_a_vm_carries_the_field_engine() -> None:
    async def body(e: PacketEngine) -> None:
        r = await e.build(ETHER_IP + [{"id": "UDP"}], flow_fe())
        assert r["binary"] and r["field_engine"]
        res = await e.doc_from_binary(r["binary"], r["field_engine"]["instructions"])
        doc = res["doc"]
        assert [i["id"] for i in doc["feInstructions"]] == ["STLVmFlowVar", "STLVmWrFlowVar"]
        assert doc["fePrarameters"] == {"cache_size": "10"} and res["warnings"] == []
        assert (await e.doc_from_binary(r["binary"], None))["doc"]["fePrarameters"] == {"cache_size": "1000"}
    with_engine(body)


def test_reconstruct_edit_keeps_values_outside_the_field() -> None:
    async def body(e: PacketEngine) -> None:
        doc = udp_doc()
        raw = bytearray(await rebuilt(e, doc))
        assert raw[22] == 33
        raw[22] = 99
        res = await e.reconstruct_edit(doc, b64(bytes(raw)), {"layer": 1, "field": "ttl"})
        assert res["warnings"] == []
        layers = res["doc"]["packet"]
        assert layers[1]["fields"] == [{"id": "ttl", "value": 99}]
        assert layers[3]["fields"][0]["value"]["generate"] == "random_ascii"
        assert layers[2] == doc["packet"][2]
        assert (await rebuilt(e, res["doc"]))[22] == 99
    with_engine(body)


def test_reconstruct_edit_takes_a_layer_without_fields() -> None:
    # A layer with no `fields` key reaches the worker only past parse_doc (which always sets it), so this goes
    # through the raw call: the edit stays field by field instead of falling back on a KeyError.
    async def body(e: PacketEngine) -> None:
        doc = udp_doc()
        raw = bytearray(await rebuilt(e, doc))
        raw[22] = 99
        bare = {**doc, "packet": [{"id": "Ether"}, *doc["packet"][1:]]}
        res = await e.call("reconstruct_edit", {"doc": bare, "binary": b64(bytes(raw)),
                                                "edited": {"layer": 1, "field": "ttl"}})
        assert res["warnings"] == []
        assert res["doc"]["packet"][1]["fields"] == [{"id": "ttl", "value": 99}]
    with_engine(body)


def test_reconstruct_edit_falls_back_on_a_class_change() -> None:
    async def body(e: PacketEngine) -> None:
        doc = udp_doc(dport=53)                      # the bytes dissect as DNS, not as Raw
        raw = bytearray(await rebuilt(e, doc))
        raw[22] = 99
        res = await e.reconstruct_edit(doc, b64(bytes(raw)), {"layer": 1, "field": "ttl"})
        assert res["warnings"][0] == ("the byte edit re-derived the packet from its bytes: generated and "
                                      "expression values became literal values")
        assert not any("generate" in n for n in walk(res["doc"]["packet"]) if isinstance(n, dict))
        assert (await rebuilt(e, res["doc"])) == bytes(raw)
        none = await e.reconstruct_edit(udp_doc(), b64(bytes(raw)), None)       # no field named
        assert none["warnings"][0].startswith("the byte edit re-derived")
        assert none["doc"]["fePrarameters"] == udp_doc()["fePrarameters"]
    with_engine(body)


def test_reconstruct_edit_emits_no_code() -> None:
    expr = "[IPOption_NOP(), IPOption_NOP(), IPOption_NOP(), IPOption_NOP()]"
    doc = parse_doc({"packet": [
        {"id": "Ether"}, {"id": "IP", "fields": [{"id": "options", "value": {"vtype": "EXPRESSION",
                                                                                "expr": expr}}]},
        {"id": "UDP", "fields": UDP_PORTS}, {"id": "Raw", "fields": [{"id": "load", "value": "abc"}]}]})

    async def body(e: PacketEngine) -> None:
        raw = bytearray(await rebuilt(e, doc))
        assert raw[34:38] == b"\x01\x01\x01\x01"
        raw[34] = 0x94                                              # the first option byte
        res = await e.reconstruct_edit(doc, b64(bytes(raw)), {"layer": 1, "field": "options"})
        assert res["warnings"] == []                                # D12 keeps the document field by field
        assert field_in(res["doc"]["packet"][1], "options") == {"vtype": "MACHINE", "base64": b64(raw[34:38])}
        assert not [n for n in walk(res["doc"]["packet"]) if isinstance(n, dict) and "expr" in n]
        again = bytearray(await rebuilt(e, res["doc"]))             # the document computes a new IP checksum
        assert again[:24] + again[26:] == raw[:24] + raw[26:]
        assert inet_sum(bytes(again[14:38])) == 0                   # and it is valid
    with_engine(body)


def test_decompile_round_trip_with_warnings() -> None:
    async def body(e: PacketEngine) -> None:
        fe = flow_fe()
        fe["instructions"].append({"id": "STLVmFixIpv4", "parameters": {"offset": "IP:0"}})
        r = await e.build(ETHER_IP + [{"id": "UDP"}], fe)
        assert r["binary"] and r["field_engine"]
        vm = r["field_engine"]["instructions"]
        dec = await e.decompile(r["binary"], vm)
        assert dec["warnings"] == [] and dec["global_parameters"] == {"cache_size": "10"}
        assert all(isinstance(v, str) for i in dec["instructions"] for v in i["parameters"].values())
        flow = dec["instructions"][0]["parameters"]
        assert flow["init_value"] == "268435457" and flow["value_list"] == "None"
        assert dec["instructions"][1]["parameters"]["is_big"] == "true"
        again = await e.build(ETHER_IP + [{"id": "UDP"}], {"instructions": dec["instructions"],
                                                           "global_parameters": dec["global_parameters"]})
        assert again["field_engine"] is not None and again["field_engine"]["instructions"] == vm
        # A key the editor has no parameter for is dropped, with a warning when it is not the default.
        first = {**vm["instructions"][0], "split_to_cores": False}
        odd = {**vm, "instructions": [first, *vm["instructions"][1:]]}
        warned = await e.decompile(r["binary"], odd)
        assert warned["warnings"] == ["instruction 0 STLVmFlowVar: split_to_cores=False is not editable here "
                                      "and was dropped"]
    with_engine(body)


def test_template_bytes_decode() -> None:
    async def body(e: PacketEngine) -> None:
        listed = await e.templates()
        assert {"id": "ICMP echo request", "name": "ICMP echo request"} in listed
        doc = await e.template("IPv4/UDP")
        assert [layer["id"] for layer in doc["packet"]] == ["Ether", "IP", "UDP"]
        want = json.loads((TEMPLATES / "IPv4" / "UDP.trp").read_text())
        assert doc["packet"] == parse_doc(want)["packet"]
        with pytest.raises(PacketError, match="unknown template") as ei:
            await e.template("no/such")
        assert ei.value.restarted is False
    with_engine(body)


def write_pcap(path: Path, frames: list[bytes]) -> None:
    """A little-endian classic pcap of Ethernet frames."""
    with path.open("wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for i, fr in enumerate(frames):
            f.write(struct.pack("<IIII", i, 0, len(fr), len(fr)) + fr)


def test_pcap_list_and_packet(tmp_path: Path) -> None:
    frames = [bytes.fromhex(FRAMES["padded"]), bytes.fromhex(FRAMES["tcp_options"]),
              bytes.fromhex(FRAMES["icmpv6"])]
    write_pcap(tmp_path / "a.pcap", frames)

    async def body(e: PacketEngine) -> None:
        page = await e.pcap_list(str(tmp_path / "a.pcap"), 1, 5)
        assert page["total"] == 3
        assert [r["index"] for r in page["rows"]] == [1, 2]
        assert page["rows"][0]["length"] == len(frames[1]) and "TCP" in page["rows"][0]["summary"]
        assert (await e.pcap_list(str(tmp_path / "a.pcap"), 0, 1))["rows"][0]["index"] == 0
        assert base64.b64decode(await e.pcap_packet(str(tmp_path / "a.pcap"), 2)) == frames[2]
        with pytest.raises(PacketError, match="no packet 7") as ei:
            await e.pcap_packet(str(tmp_path / "a.pcap"), 7)
        assert ei.value.restarted is False
    with_engine(body)


def test_pcap_list_summarises_only_the_first_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A zero-filled IPv4 record dissects as IPv6 hop-by-hop options, one Pad1 per byte: summarising all 70 000
    # bytes took seconds. The list's summary dissects at most SUMMARY_BYTES; the row keeps the full length.
    from trex_web_gui.trexapi import packet_worker
    from trex_web_gui.trexapi.loader import load
    load()
    import scapy.layers.l2 as l2
    given: list[int] = []
    real_ether = l2.Ether

    def recording_ether(data: bytes) -> Any:
        """Record how many bytes the summary dissects, then dissect them."""
        given.append(len(data))
        return real_ether(data)
    monkeypatch.setattr(l2, "Ether", recording_ether)
    ipv4_ether = bytes.fromhex("020000000002" "020000000001" "0800")
    write_pcap(tmp_path / "jumbo.pcap", [ipv4_ether.ljust(70_000, b"\x00")])
    started = time.monotonic()
    bare = packet_worker.Engine.__new__(packet_worker.Engine)   # pcap_list uses no service state
    page = packet_worker.Engine.pcap_list(bare,
                                          {"path": str(tmp_path / "jumbo.pcap"), "offset": 0, "limit": 10})
    assert time.monotonic() - started < 3.0
    assert given and max(given) <= packet_worker.SUMMARY_BYTES
    assert page["ok"] is True and page["total"] == 1
    # The reader yields at most scapy's MTU (65535) bytes of a record: the "cut at 65535 when loaded" warning
    # of the capture scan. The row's length is that whole record, not the summarised prefix.
    assert page["rows"][0]["length"] == 65535 and "0.0.0.0 > 0.0.0.0" in page["rows"][0]["summary"]


def test_pcap_list_not_a_pcap(tmp_path: Path) -> None:
    (tmp_path / "x.pcap").write_bytes(b"this is not a capture file at all")

    async def body(e: PacketEngine) -> None:
        with pytest.raises(PacketError, match="^not a pcap: ") as ei:
            await e.pcap_list(str(tmp_path / "x.pcap"), 0, 10)
        assert ei.value.restarted is False
        assert (await e.build(ETHER_IP, None))["binary"]
    with_engine(body)


def test_to_stream_shapes() -> None:
    async def body(e: PacketEngine) -> None:
        s = await e.to_stream(ETHER_IP + [{"id": "UDP"}], flow_fe())
        assert sorted(s) == ["packet", "vm"] and s["packet"]["meta"] == ""
        assert len(base64.b64decode(s["packet"]["binary"])) == 42
        assert "split_by_var" not in s["vm"] and s["vm"]["cache"] == 10
        assert [i["type"] for i in s["vm"]["instructions"]] == ["flow_var", "write_flow_var"]
        bare = await e.to_stream(ETHER_IP, None)
        assert bare["vm"] == {"instructions": []}
        for packet_model, fe, text in (
                ([{"id": "Ether"}, {"id": "IP", "fields": [{"id": "ttl", "value": "abc"}]}], None,
                 "not an integer"),
                (ETHER_IP, flow_fe("10", "1"), "lower than min")):
            with pytest.raises(PacketError, match=text) as ei:
                await e.to_stream(packet_model, fe)
            assert ei.value.restarted is False
    with_engine(body)


def test_build_data_is_order_independent() -> None:
    async def body(e: PacketEngine) -> None:
        a, b = await e.template("IPv4/TCP"), await e.template("DHCP/DHCP-ACK")
        first = await e.build(*model_of(a))
        await e.build(*model_of(b))
        again = await e.build(*model_of(a))
        assert first["data"] == again["data"] and first["binary"] == again["binary"]
    with_engine(body)


def test_flag_values_stay_fields() -> None:
    """IP DF and TCP PA/SA flags are Scapy FlagValue objects, not ints; they must stay fields."""
    async def body(e: PacketEngine) -> None:
        for name, ids in (("flags_pa", ["Ether", "IP", "TCP", "Raw"]),
                          ("flags_sa", ["Ether", "IP", "TCP"])):
            raw = bytes.fromhex(FRAMES[name])
            res = await e.doc_from_binary(b64(raw), None)
            assert [layer["id"] for layer in res["doc"]["packet"]] == ids and res["warnings"] == [], name
            assert await rebuilt(e, res["doc"]) == raw
        ip = (await e.doc_from_binary(b64(bytes.fromhex(FRAMES["flags_pa"])), None))["doc"]["packet"][1]
        assert {"id": "flags", "value": 2} in ip["fields"]
    with_engine(body)


def test_reconstruct_edit_of_the_tcp_flags_byte() -> None:
    async def body(e: PacketEngine) -> None:
        raw = bytearray.fromhex(FRAMES["flags_pa"])
        doc = (await e.doc_from_binary(b64(bytes(raw)), None))["doc"]
        assert raw[47] == 0x18                         # PSH|ACK
        raw[47] = 0x10
        res = await e.reconstruct_edit(doc, b64(bytes(raw)), {"layer": 2, "field": "flags"})
        assert res["warnings"] == []
        assert {"id": "flags", "value": 16} in res["doc"]["packet"][2]["fields"]
        assert (await rebuilt(e, res["doc"]))[47] == 0x10          # the checksum is recomputed
    with_engine(body)


def test_pcap_error_mid_file_is_not_a_pcap(tmp_path: Path) -> None:
    shb = struct.pack("<IIIHHqI", 0x0A0D0D0A, 28, 0x1A2B3C4D, 1, 0, -1, 28)
    epb = struct.pack("<IIIIIIIII", 6, 36, 0, 0, 0, 0, 0, 0, 36)          # an EPB naming a missing interface
    (tmp_path / "bad.pcapng").write_bytes(shb + epb)

    async def body(e: PacketEngine) -> None:
        with pytest.raises(PacketError, match="^not a pcap: ") as ei:
            await e.pcap_list(str(tmp_path / "bad.pcapng"), 0, 10)
        assert ei.value.restarted is False
    with_engine(body)


def test_definitions_list_no_lldp_layers() -> None:
    """Pin of U2 (existing behaviour): the contrib is never loaded, so the worker offers no LLDP layer class
    (D9); the `trex-web-gui/LLDP` template is Ether / Raw for that reason."""
    async def body(e: PacketEngine) -> None:
        d = await e.definitions()
        assert [i for i in d["layerIds"] if "LLDP" in i.upper()] == []
        assert [p["id"] for p in d["protocols"] if "LLDP" in p["id"].upper()] == []
    with_engine(body)


def test_lldp_profile_json_parses() -> None:
    """docs/profiles/lldp_30s.json is one valid continuous stream at 1/30 pps (a frame every 30 s)."""
    from trex_web_gui.studio.streams import parse_streams
    path = Path(__file__).resolve().parents[2] / "docs" / "profiles" / "lldp_30s.json"
    streams = parse_streams(json.loads(path.read_text()))
    assert len(streams) == 1
    assert streams[0]["mode"] == {"type": "continuous", "rate": {"type": "pps", "value": 1 / 30}}
    raw = base64.b64decode(streams[0]["packet"]["binary"])
    assert raw[:6] == bytes.fromhex("0180c200000e") and raw[12:14] == b"\x88\xcc"


# --- the editor's decode cap (DEFERRED rc4-release-prep-106) ---

IPV4_ETHER = bytes.fromhex("020000000002" "020000000001" "0800")


def udp_record(n: int) -> bytes:
    """A well-formed Ether/IP/UDP record of n bytes whose payload dissects as Raw."""
    from trex_web_gui.trexapi.loader import load
    load()
    from scapy.all import IP, UDP, Ether, Raw
    body = bytes(range(256)) * (n // 256 + 1)
    return bytes(Ether() / IP(proto=17) / UDP() / Raw(body[:n - 42]))


def engine_worker() -> Any:
    """A bare worker Engine (the real service, in this process)."""
    from trex_web_gui.trexapi import packet_worker
    return packet_worker.Engine()


def test_doc_from_binary_dissects_at_most_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from trex_web_gui.trexapi import packet_worker
    eng = engine_worker()
    import scapy.layers.l2 as l2
    given: list[int] = []
    real_ether = l2.Ether

    def recording_ether(data: bytes) -> Any:
        """Record how many bytes are dissected, then dissect them."""
        given.append(len(data))
        return real_ether(data)
    monkeypatch.setattr(l2, "Ether", recording_ether)
    raw = IPV4_ETHER.ljust(70_000, b"\x00")
    res = eng.doc_from_binary({"binary": b64(raw), "vm": None})
    cap = packet_worker.EDITOR_DECODE_BYTES
    assert given and all(n <= cap or n == packet_worker.ETHER_LEN for n in given)
    last = res["doc"]["packet"][-1]
    assert last["id"] == "Raw"
    # The zero-filled head cannot be dissected field by field, so the document is Ether plus one Raw that
    # holds everything after the Ether header, tail included.
    assert base64.b64decode(last["fields"][0]["value"]["base64"]).endswith(raw[cap:])
    assert packet_worker.TRUNCATED_WARNING.format(n=70_000) in res["warnings"]


def test_capped_record_rebuilds_byte_identical() -> None:
    from trex_web_gui.trexapi import packet_worker

    async def body(e: PacketEngine) -> None:
        for raw in (udp_record(20_000), udp_record(65_535)):
            res = await e.doc_from_binary(b64(raw), None)
            assert packet_worker.TRUNCATED_WARNING.format(n=len(raw)) in res["warnings"]
            assert await rebuilt(e, res["doc"]) == raw
    with_engine(body)


def test_build_of_a_capped_record_has_a_valid_structure() -> None:
    """The build's field tree of a capped document maps every layer, the raw tail included, onto the bytes."""
    from trex_web_gui.trexapi import packet_worker

    async def body(e: PacketEngine) -> None:
        raw = udp_record(20_000)
        res = await e.doc_from_binary(b64(raw), None)
        assert packet_worker.TRUNCATED_WARNING.format(n=len(raw)) in res["warnings"]
        packet_model, fe = model_of(res["doc"])
        built = await e.build(packet_model, fe)
        assert built["error"] is None and base64.b64decode(built["binary"]) == raw
        assert built["data"]
        assert [(layer["id"], layer["valid_structure"]) for layer in built["data"]] == [
            (layer["id"], True) for layer in built["data"]]
    with_engine(body)


def test_decode_cap_is_the_largest_studio_packet() -> None:
    from trex_web_gui.studio import streams
    from trex_web_gui.trexapi import packet_worker
    assert packet_worker.EDITOR_DECODE_BYTES == streams.PKT_MAX


@pytest.mark.parametrize("name", ["_fully_define", "_packet_model_to_scapy_packet"])
def test_engine_refuses_a_service_without_a_private_name(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A vendor refresh that renames a private service method the cap relies on fails at worker start."""
    from trex_web_gui.trexapi.loader import load
    load()
    from trex.scapy_server.scapy_service import Scapy_service
    monkeypatch.delattr(Scapy_service, name)
    with pytest.raises(RuntimeError, match=name):
        engine_worker()


def test_cap_boundary() -> None:
    from trex_web_gui.trexapi import packet_worker
    cap = packet_worker.EDITOR_DECODE_BYTES

    async def body(e: PacketEngine) -> None:
        at = await e.doc_from_binary(b64(udp_record(cap)), None)
        assert not any("only the first" in w for w in at["warnings"])
        assert [layer["id"] for layer in at["doc"]["packet"]] == ["Ether", "IP", "UDP", "Raw"]
        over_raw = udp_record(cap + 1)
        over = await e.doc_from_binary(b64(over_raw), None)
        assert packet_worker.TRUNCATED_WARNING.format(n=cap + 1) in over["warnings"]
        last = over["doc"]["packet"][-1]
        assert base64.b64decode(last["fields"][0]["value"]["base64"]) == over_raw[cap:]
        assert await rebuilt(e, over["doc"]) == over_raw
    with_engine(body)


def test_capped_record_field_edit_keeps_the_tail() -> None:
    from trex_web_gui.trexapi import packet_worker
    cap = packet_worker.EDITOR_DECODE_BYTES

    async def body(e: PacketEngine) -> None:
        raw = udp_record(20_000)
        doc = (await e.doc_from_binary(b64(raw), None))["doc"]
        edited = bytearray(raw)
        edited[22] = 99                                       # IP.ttl
        res = await e.reconstruct_edit(doc, b64(bytes(edited)), {"layer": 1, "field": "ttl"})
        assert packet_worker.FALLBACK_WARNING not in res["warnings"]
        assert {"id": "ttl", "value": 99} in res["doc"]["packet"][1]["fields"]
        out = await rebuilt(e, res["doc"])
        from trex_web_gui.trexapi.loader import load
        load()
        from scapy.all import IP
        want = IP(bytes(edited)[14:])
        want.chksum = None
        assert len(out) == len(edited) and out[cap:] == bytes(edited)[cap:]
        assert out[22] == 99 and out[16:18] == bytes(edited)[16:18]          # IP.len over the whole record
        assert out[24:26] == bytes(IP(bytes(want)))[10:12]                 # IP.chksum recomputed
    with_engine(body)


def test_field_edit_on_a_jumbo_record_in_time() -> None:
    from trex_web_gui.trexapi import packet_worker

    async def body(e: PacketEngine) -> None:
        raw = udp_record(65_535)
        doc = (await e.doc_from_binary(b64(raw), None))["doc"]
        edited = bytearray(raw)
        edited[22] = 99
        res = await e.reconstruct_edit(doc, b64(bytes(edited)), {"layer": 1, "field": "ttl"})
        assert packet_worker.FALLBACK_WARNING not in res["warnings"]
        assert (await rebuilt(e, res["doc"]))[22] == 99
        zeros = IPV4_ETHER.ljust(70_000, b"\x00")
        zopened = await e.doc_from_binary(b64(zeros), None)
        assert packet_worker.TRUNCATED_WARNING.format(n=len(zeros)) in zopened["warnings"]
        zdoc = zopened["doc"]
        zed = bytearray(zeros)
        zed[22] = 99
        zres = await e.reconstruct_edit(zdoc, b64(bytes(zed)), {"layer": 1, "field": "ttl"})
        # Its ihl is 0, so vendor patch 14 gives the IP layer no options: the field edit applies as one.
        # Before the patch the options list read the whole frame, the model did not rebuild to these bytes,
        # and the edit fell back to re-deriving the packet from them (FALLBACK_WARNING).
        assert packet_worker.FALLBACK_WARNING not in zres["warnings"]
        assert await rebuilt(e, zres["doc"]) == bytes(zed)
        assert e.starts == 1
    with_engine(body)


def test_byte_edit_in_the_tail_stays_capped() -> None:
    from trex_web_gui.trexapi import packet_worker

    async def body(e: PacketEngine) -> None:
        raw = udp_record(20_000)
        doc = (await e.doc_from_binary(b64(raw), None))["doc"]
        edited = bytearray(raw)
        edited[19_000] ^= 0xFF
        res = await e.reconstruct_edit(doc, b64(bytes(edited)), None)
        assert res["doc"]["packet"][-1]["id"] == "Raw"
        assert packet_worker.TRUNCATED_WARNING.format(n=20_000) in res["warnings"]
        assert await rebuilt(e, res["doc"]) == bytes(edited)
    with_engine(body)


def test_pcap_packet_opens_a_jumbo_record_in_time(tmp_path: Path) -> None:
    write_pcap(tmp_path / "jumbo.pcap", [IPV4_ETHER.ljust(70_000, b"\x00")])

    async def body(e: PacketEngine) -> None:
        binary = await e.pcap_packet(str(tmp_path / "jumbo.pcap"), 0)
        res = await e.doc_from_binary(binary, None)
        assert res["doc"]["packet"][-1]["id"] == "Raw" and e.starts == 1
    with_engine(body)


def test_studio_open_of_a_capped_stream_with_a_vm() -> None:
    from trex_web_gui.trexapi import packet_worker

    async def body(e: PacketEngine) -> None:
        raw = udp_record(20_000)
        opened = await e.doc_from_binary(b64(raw), None)
        assert packet_worker.TRUNCATED_WARNING.format(n=len(raw)) in opened["warnings"]
        model = opened["doc"]
        packet_model, _ = model_of(model)
        fe = {"instructions": [
            {"id": "STLVmFlowVar", "parameters": {"name": "v", "min_value": "0", "max_value": "255",
                                                  "size": "1", "op": "inc"}},
            {"id": "STLVmWrFlowVar", "parameters": {"fv_name": "v", "pkt_offset": "15000"}}],
            "global_parameters": {"cache_size": "10"}}
        built = await e.build(packet_model, fe)
        assert built["field_engine"] and built["field_engine"]["instructions"], built
        res = await e.doc_from_binary(built["binary"], built["field_engine"]["instructions"])
        assert res["doc"]["feInstructions"]
    with_engine(body)


def test_building_an_opened_jumbo_record_stays_in_time(tmp_path: Path) -> None:
    """The editor builds the document it opened; the build's field tree is bounded too (no restart)."""
    raw = IPV4_ETHER.ljust(70_000, b"\x00")[:65_535]
    write_pcap(tmp_path / "jumbo.pcap", [raw])

    async def body(e: PacketEngine) -> None:
        binary = await e.pcap_packet(str(tmp_path / "jumbo.pcap"), 0)
        doc = (await e.doc_from_binary(binary, None))["doc"]
        packet_model, _ = model_of(doc)
        built = await e.build(packet_model, None)
        assert built["error"] is None and base64.b64decode(built["binary"]) == raw
        fe = flow_fe()
        fe["instructions"][1]["parameters"]["pkt_offset"] = "20"
        with_vm = await e.build(packet_model, fe)
        assert with_vm["error"] is None and with_vm["field_engine"]["instructions"]
        assert base64.b64decode(with_vm["binary"]) == raw
        assert e.starts == 1
    with_engine(body)


# --- the capture viewer's tree runs here, not in the backend (gemini-security-review 2026-11-01 SEC-03) ---

NO_OPTIONS_IP = {"layer": "IP", "rows": [], "padding": 0, "length": 0, "padded": 0, "problem": None}


def test_tree_op_matches_the_backend_tree() -> None:
    """The worker's tree is the service's, but for the texts the worker rewrites: here IP flags 0 shows 0."""
    from trex_web_gui.trexapi import decode
    raw = udp_record(200)
    res = engine_worker().tree({"binary": b64(raw)})
    assert res.pop("ok") is True
    want = decode.tree(raw)
    flags = next(f for f in want["data"][1]["fields"] if f["id"] == "flags")
    assert flags["hvalue"] == ""
    flags["hvalue"] = "0"
    opts = next(f for f in want["data"][1]["fields"] if f["id"] == "options")
    opts["value"] = {"vtype": "MACHINE", "base64": ""}          # the worker adds the editable options field
    got = next(f for f in res["data"][1]["fields"] if f["id"] == "options")
    assert got.pop("options") == NO_OPTIONS_IP
    assert res == want


def test_tree_op_dissects_at_most_the_cap_and_keeps_every_byte(monkeypatch: pytest.MonkeyPatch) -> None:
    from trex_web_gui.trexapi import packet_worker
    eng = engine_worker()
    import scapy.layers.l2 as l2
    given: list[int] = []
    real_ether = l2.Ether

    def recording_ether(data: bytes) -> Any:
        """Record how many bytes are dissected, then dissect them."""
        given.append(len(data))
        return real_ether(data)
    monkeypatch.setattr(l2, "Ether", recording_ether)

    def whole(*args: Any) -> Any:
        raise AssertionError("reconstruct_pkt dissects the whole frame")
    monkeypatch.setattr(eng.svc, "reconstruct_pkt", whole)
    cap = packet_worker.EDITOR_DECODE_BYTES
    tail = bytes(range(256)) * 81                      # not uniform: a reordered frame cannot compare equal
    raw = IPV4_ETHER.ljust(cap, b"\x00") + tail
    res = eng.tree({"binary": b64(raw)})
    assert given and all(n <= cap for n in given)
    assert base64.b64decode(res["binary"]) == raw
    assert res["data"][-1]["id"] == "Raw"


def test_tree_op_keeps_a_padded_heads_bytes_in_order() -> None:
    # IP.len 100 leaves the rest of the head as Padding, which Scapy builds after the whole packet: the tail
    # must not be nested under it (PR 14 review, I-1).
    from scapy.all import IP, UDP, Ether
    head = bytes(Ether() / IP(len=100) / UDP())
    raw = head.ljust(packet_worker_cap(), b"\x00") + bytes(range(256)) * 11
    res = engine_worker().tree({"binary": b64(raw)})
    assert base64.b64decode(res["binary"]) == raw
    layers = [(d["id"], d["offset"]) for d in res["data"]]
    assert layers == [("Ether", 0), ("IP", 14), ("UDP", 34), ("Raw", 42)]


def packet_worker_cap() -> int:
    """The editor's decode cap, EDITOR_DECODE_BYTES."""
    from trex_web_gui.trexapi import packet_worker
    return packet_worker.EDITOR_DECODE_BYTES


def test_tree_op_refuses_a_frame_shorter_than_ethernet() -> None:
    async def body(e: PacketEngine) -> None:
        with pytest.raises(PacketError) as info:
            await e.tree(b64(bytes(5)))
        assert not info.value.restarted and "unpack requires a buffer" in str(info.value)
    with_engine(body)


# look-7: with IP options that need padding, the fork records each later layer's offset before IP.post_build
# pads, so the editor's byte highlights ran early by the pad. The worker now takes layer offsets from the
# bytes.
ZZ = {"vtype": "BYTES", "base64": base64.b64encode(b"zz").decode()}


def options_model(opts: str | None, top: str = "UDP", load: dict[str, Any] = ZZ) -> list[dict[str, Any]]:
    """Ether / IP(options=`opts`, an EXPRESSION; none when None) / `top` / Raw(load=`load`)."""
    ip: dict[str, Any] = {"id": "IP"}
    if opts is not None:
        ip["fields"] = [{"id": "options", "value": {"vtype": "EXPRESSION", "expr": opts}}]
    return [{"id": "Ether"}, ip, {"id": top}, {"id": "Raw", "fields": [{"id": "load", "value": load}]}]


def offsets(r: Any) -> list[tuple[str, int]]:
    """Each built layer's (id, offset), outermost first."""
    return [(layer["id"], layer["offset"]) for layer in r["data"]]


ONE_NOP = "[IPOption_NOP()]"
FOUR_NOPS = "[IPOption_NOP(), IPOption_NOP(), IPOption_NOP(), IPOption_NOP()]"


def test_layer_offsets_follow_padded_ip_options() -> None:
    async def body(e: PacketEngine) -> None:
        r = await e.build(options_model(ONE_NOP), None)
        assert r["error"] is None and len(base64.b64decode(r["binary"])) == 48
        assert offsets(r) == [("Ether", 0), ("IP", 14), ("UDP", 38), ("Raw", 46)]
    with_engine(body)


def test_layer_offsets_without_padding_are_unchanged() -> None:
    async def body(e: PacketEngine) -> None:
        r = await e.build(options_model(FOUR_NOPS), None)
        assert offsets(r) == [("Ether", 0), ("IP", 14), ("UDP", 38), ("Raw", 46)]
        r = await e.build(options_model(None), None)
        assert offsets(r) == [("Ether", 0), ("IP", 14), ("UDP", 34), ("Raw", 42)]
    with_engine(body)


def test_first_layer_that_dissects_differently_still_gets_its_byte_offset() -> None:
    """UDP's default ports are 53, so the bytes after it read as DNS: Raw does not match, and still starts
    at 46."""
    async def body(e: PacketEngine) -> None:
        raw = (await e.build(options_model(ONE_NOP), None))["data"][3]
        assert raw["valid_structure"] is False and raw["offset"] == 46
    with_engine(body)


def test_field_offsets_stay_relative_to_their_layer() -> None:
    async def body(e: PacketEngine) -> None:
        udp = (await e.build(options_model(ONE_NOP), None))["data"][2]
        assert {f["id"]: f["offset"] for f in udp["fields"]}.items() >= {"sport": 0, "dport": 2}.items()
    with_engine(body)


def test_layer_offsets_past_the_decode_cap_fall_back() -> None:
    from trex_web_gui.trexapi import packet_worker
    long = {"vtype": "BYTES", "base64": base64.b64encode(bytes(packet_worker.EDITOR_DECODE_BYTES)).decode()}

    async def body(e: PacketEngine) -> None:
        r = await e.build(options_model(ONE_NOP, load=long), None)
        assert r["error"] is None
        assert offsets(r)[2] == ("UDP", 38)
    with_engine(body)


def test_layer_offsets_when_the_model_outlasts_its_bytes() -> None:
    """A trailing empty Raw has no bytes of its own: the byte chain ends a layer early. That layer takes the
    service's offset moved by the last matched layer's correction (57 + 3 = 60, its true start), so the
    offsets stay in order and the layer before it keeps its hex range (the frontend ends a layer where the
    next one starts)."""
    empty_raw = {"id": "Raw", "fields": [{"id": "load", "value": {"vtype": "BYTES", "base64": ""}}]}

    async def body(e: PacketEngine) -> None:
        r = await e.build([*options_model(ONE_NOP, top="TCP"), empty_raw], None)
        assert r["error"] is None
        assert offsets(r) == [("Ether", 0), ("IP", 14), ("TCP", 38), ("Raw", 58), ("Raw", 60)]
    with_engine(body)


def test_capture_tree_offsets_follow_padded_ip_options() -> None:
    async def body(e: PacketEngine) -> None:
        binary = (await e.build(options_model(ONE_NOP), None))["binary"]
        assert offsets(await e.tree(binary))[2] == ("UDP", 38)


# Capture-shaped frames of the three contribs the loader loads (Scapy fork patches, Task 2), kept as bytes so
# that building them loads nothing: GENEVE (UDP 49152 -> 6081) carrying Ether/IP, GTP-U (UDP 2152) carrying
# IP, and an LACPDU (slow protocols, 01:80:c2:00:00:02, EtherType 0x8809).
CONTRIB_FRAMES = {
    "GENEVE": bytes.fromhex(
        "0000000100000000000200000800450000460001000040113aa51000000130000001c00017c100327a6b000065580000"
        "00000000000100000000000200000800450000140001000040003ae81000000130000001"),
    "GTP_U": bytes.fromhex(
        "0000000100000000000200000800450000380001000040113ab310000001300000010868086800247dc130ff00140000"
        "0000450000140001000040003ae81000000130000001"),
    "LACP": bytes.fromhex(
        "0180c2000002000000020000880901010114000000000000000000000000000000000000021400000000000000000000"
        "000000000000000003100000000000000000000000000000000000000000000000000000000000000000000000000000"
        "00000000000000000000000000000000000000000000000000000000"),
}
CONTRIB_TREES = {
    "GENEVE": ["Ether", "IP", "UDP", "GENEVE", "Ether", "IP"],
    "GTP_U": ["Ether", "IP", "UDP", "GTP_U_Header", "IP"],
    "LACP": ["Ether", "SlowProtocol", "LACP"],
}


def test_definitions_list_the_loaded_contribs() -> None:
    """GENEVE, GTP and LACP are in the fork; the loader loads them, so the editor offers them. GTP_U_Header
    subclasses GTPHeader, and get_definitions lists direct Packet subclasses only: it is a layer id, not a
    protocol."""
    async def body(e: PacketEngine) -> None:
        d = await e.definitions()
        assert {"GENEVE", "GTPHeader", "SlowProtocol", "LACP"} <= {p["id"] for p in d["protocols"]}
        assert "GTP_U_Header" in d["layerIds"]
    with_engine(body)


@pytest.mark.parametrize("name", sorted(CONTRIB_FRAMES))
def test_capture_tree_names_geneve_gtp_lacp(name: str) -> None:
    async def body(e: PacketEngine) -> None:
        tree = await e.tree(b64(CONTRIB_FRAMES[name]))
        assert [layer["id"] for layer in tree["data"]][:len(CONTRIB_TREES[name])] == CONTRIB_TREES[name]
    with_engine(body)


def test_editor_builds_gtp_u_and_lacp() -> None:
    async def body(e: PacketEngine) -> None:
        for model in ([{"id": "Ether"}, {"id": "IP"}, {"id": "UDP"}, {"id": "GTP_U_Header"}, {"id": "IP"}],
                      [{"id": "Ether"}, {"id": "SlowProtocol"}, {"id": "LACP"}]):
            r = await e.build(model, None)
            assert r["error"] is None, r["error"]
            ids = [layer["id"] for layer in model]
            assert [layer["id"] for layer in (await e.tree(r["binary"]))["data"]][:len(ids)] == ids


def test_capture_tree_of_a_zero_filled_ipv4_frame_has_no_options() -> None:
    """ihl 0 means no IP options (vendor patch 14): the fork used to read the whole frame as options, about
    2 s and 270 MiB for this frame (SEC-03)."""
    from trex_web_gui.trexapi import packet_worker
    frame = bytes(12) + b"\x08\x00" + bytes(packet_worker.EDITOR_DECODE_BYTES - 14)

    async def body(e: PacketEngine) -> None:
        ip = next(layer for layer in (await e.tree(b64(frame)))["data"] if layer["id"] == "IP")
        assert next(f for f in ip["fields"] if f["id"] == "options")["hvalue"] == "[]"
    with_engine(body)


# IP options as Wireshark reads them: Scapy 2.4.3 parses the zeros after End of Options as more EOLs, and its
# expression names every field. The shown text (hvalue) is a summary; the value stays the service's
# expression.
# The option bytes are those of a user's capture (01 | 94 04 00 00 | 00 | 00 00); the addresses are test ones.
# The frame, ROUTER_ALERT_FRAME, lives in tests/support.py: the contract tests use it too.


RA = ROUTER_ALERT_FRAME[34:42]       # the frame's option bytes: NOP, Router Alert, EOL, two zeros


def field_in(layer: dict[str, Any], name: str) -> Any:
    """The value of the field `name` of one document layer."""
    return next(f["value"] for f in layer["fields"] if f["id"] == name)


def field_of(r: Any, layer: str, name: str) -> dict[str, Any]:
    """The field `name` of the first layer `layer` in a tree or build result."""
    return next(f for d in r["data"] if d["id"] == layer for f in d["fields"] if f["id"] == name)


def test_ip_options_show_as_a_summary_with_padding_after_eol() -> None:
    async def body(e: PacketEngine) -> None:
        opts = field_of(await e.tree(b64(ROUTER_ALERT_FRAME)), "IP", "options")
        assert opts["hvalue"] == "NOP, Router Alert (alert=router_shall_examine_packet), EOL, padding 2 bytes"
        assert opts["value"] == {"vtype": "MACHINE", "base64": b64(RA)}     # exact bytes, not an expression
    with_engine(body)


def test_built_ip_options_show_the_padding_ip_adds() -> None:
    """One NOP is padded to four bytes with zeros: Wireshark reads NOP, EOL and two bytes of padding."""
    async def body(e: PacketEngine) -> None:
        assert field_of(await e.build(options_model(ONE_NOP), None), "IP", "options")["hvalue"] == (
            "NOP, EOL, padding 2 bytes")
    with_engine(body)


def test_an_option_after_eol_is_listed_not_counted_as_padding() -> None:
    async def body(e: PacketEngine) -> None:
        model = options_model("[IPOption_EOL(), IPOption_NOP(), IPOption_EOL(), IPOption_EOL()]")
        r = await e.build(model, None)
        assert field_of(r, "IP", "options")["hvalue"] == "EOL, NOP, EOL, EOL"
    with_engine(body)


def test_an_unnamed_option_shows_its_number_and_value() -> None:
    raw = bytearray(ROUTER_ALERT_FRAME)
    raw[35:39] = bytes.fromhex("19040102")      # in place of the Router Alert: option 25, length 4, 01 02

    async def body(e: PacketEngine) -> None:
        opts = field_of(await e.tree(b64(bytes(raw))), "IP", "options")["hvalue"]
        assert opts.startswith("NOP, option 25 (value=") and opts.endswith("), EOL, padding 2 bytes")
    with_engine(body)


def test_no_options_and_empty_flags_read_as_such() -> None:
    """IP flags 0 shows 0, not an empty cell; DF still shows its name; no options stays []."""
    async def body(e: PacketEngine) -> None:
        r = await e.tree(b64(ROUTER_ALERT_FRAME))
        assert field_of(r, "IP", "flags")["hvalue"] == "0"
        df = await e.build([{"id": "Ether"}, {"id": "IP", "fields": [{"id": "flags", "value": 2}]}], None)
        assert field_of(df, "IP", "flags")["hvalue"] == "DF"
        assert field_of(await e.build(options_model(None), None), "IP", "options")["hvalue"] == "[]"
    with_engine(body)


# --- IP and TCP options as exact bytes (options-editing Task 2) ---

def machine(data: bytes) -> dict[str, str]:
    """A MACHINE value of `data`."""
    return {"vtype": "MACHINE", "base64": b64(data)}


def with_options(layer: str, data: bytes, top: str | None = None) -> list[dict[str, Any]]:
    """Ether / IP / UDP (TCP when `layer` is TCP) / Raw, with the options of `layer` MACHINE `data`."""
    top = top or ("TCP" if layer == "TCP" else "UDP")
    fields = [{"id": "options", "value": machine(data)}]
    ports = [{"id": "sport", "value": 4000}, {"id": "dport", "value": 5000}]    # not 53: that reads as DNS
    stack: list[dict[str, Any]] = [{"id": "Ether"}, {"id": "IP"}, {"id": top, "fields": ports},
                                   {"id": "Raw", "fields": [{"id": "load", "value": ZZ}]}]
    stack[1 if layer == "IP" else 2]["fields"] = fields
    return stack


async def built(e: PacketEngine, model: list[dict[str, Any]], fe: Any = None) -> dict[str, Any]:
    """A build that must succeed."""
    r = await e.build(model, fe)
    assert r["error"] is None, r["error"]
    return r


async def doc_of(e: PacketEngine, raw: bytes) -> dict[str, Any]:
    """The document `doc_from_binary` gives for `raw`, which must open without warnings."""
    res = await e.doc_from_binary(b64(raw), None)
    assert res["warnings"] == [], res["warnings"]
    return res["doc"]


def inet_sum(data: bytes) -> int:
    """The Internet checksum of `data`: 0 for a header or segment whose checksum field is right."""
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total > 0xFFFF:
        total = (total & 0xFFFF) + (total >> 16)
    return total ^ 0xFFFF


def test_a_capture_with_ip_options_opens_field_by_field() -> None:
    async def body(e: PacketEngine) -> None:
        res = await e.doc_from_binary(b64(ROUTER_ALERT_FRAME), None)
        assert [layer["id"] for layer in res["doc"]["packet"]] == ["Ether", "IP", "ICMP"]
        assert res["warnings"] == []
        assert field_in(res["doc"]["packet"][1], "options") == machine(RA)
        assert await rebuilt(e, res["doc"]) == ROUTER_ALERT_FRAME
    with_engine(body)


PADDED_12 = bytes.fromhex("94040000") + bytes(8)        # Router Alert, EOL, then 7 more zeros: ihl 8


def test_extra_padding_round_trips() -> None:
    """Review Focus 1: more zeros than the boundary needs open and save as the same 12 bytes."""
    async def body(e: PacketEngine) -> None:
        frame = base64.b64decode((await built(e, with_options("IP", PADDED_12)))["binary"])
        assert frame[14] == 0x48 and frame[34:46] == PADDED_12
        doc = await doc_of(e, frame)
        assert field_in(doc["packet"][1], "options") == machine(PADDED_12)
        assert await rebuilt(e, doc) == frame
        edited = bytearray(frame)
        edited[38] = 1                                              # the alert value, same length
        res = await e.reconstruct_edit(doc, b64(bytes(edited)), {"layer": 1, "field": "options"})
        assert res["warnings"] == []
        assert field_in(res["doc"]["packet"][1], "options") == machine(bytes(edited[34:46]))
    with_engine(body)


def test_forty_bytes_of_options_round_trip() -> None:
    """Review Focus 2: ihl 15."""
    forty = bytes.fromhex("94040000") * 10

    async def body(e: PacketEngine) -> None:
        frame = base64.b64decode((await built(e, with_options("IP", forty)))["binary"])
        assert frame[14] == 0x4F
        doc = await doc_of(e, frame)
        assert field_in(doc["packet"][1], "options") == machine(forty)
        assert await rebuilt(e, doc) == frame
    with_engine(body)


def test_a_document_over_40_bytes_does_not_build() -> None:
    async def body(e: PacketEngine) -> None:
        for layer in ("IP", "TCP"):
            r = await e.build(with_options(layer, bytes(41)), None)
            assert r["binary"] is None and "options are 41 bytes; the header holds 40 at most" in r["error"]
        await built(e, with_options("IP", bytes(40)))              # the bound is 40 itself
    with_engine(body)


def test_options_that_are_not_base64_do_not_build() -> None:
    async def body(e: PacketEngine) -> None:
        for bad in ("a!b", "AAA", "QUJD!REVG", 7):     # junk, bad padding, lenient-decodable junk, not text
            model = with_options("IP", b"")
            model[1]["fields"][0]["value"] = {"vtype": "MACHINE", "base64": bad}
            r = await e.build(model, None)
            assert r["binary"] is None and "the options value is not valid base64" in r["error"], bad
    with_engine(body)


def test_an_icmp_error_quoting_ip_options_round_trips() -> None:
    """Review Focus 3: the quoted header's options are a MACHINE field of IPerror."""
    model = [{"id": "Ether"}, {"id": "IP"}, {"id": "ICMP", "fields": [{"id": "type", "value": 3}]},
             {"id": "IPerror", "fields": [{"id": "options", "value": machine(RA)}]}, {"id": "UDPerror"}]

    async def body(e: PacketEngine) -> None:
        frame = base64.b64decode((await built(e, model))["binary"])
        doc = await doc_of(e, frame)
        assert [layer["id"] for layer in doc["packet"]] == ["Ether", "IP", "ICMP", "IPerror", "UDPerror"]
        assert field_in(doc["packet"][3], "options") == machine(RA)
        assert await rebuilt(e, doc) == frame
    with_engine(body)


# MSS, NOP, a SAck with 5 value bytes, 1e 01, two zeros
TCP_OPTIONS = bytes.fromhex("020405b4" "01" "0507aabbccddee" "1e01" "0000")


def test_tcp_options_round_trip_including_other_rows() -> None:
    """Review Focus 4: a SAck of 5 value bytes and a length byte of 1 are "other" rows, kept byte for byte."""
    async def body(e: PacketEngine) -> None:
        r = await built(e, with_options("TCP", TCP_OPTIONS))
        frame = base64.b64decode(r["binary"])
        assert frame[34 + 12] >> 4 == 9
        doc = await doc_of(e, frame)
        tcp = doc["packet"][2]
        assert [f["id"] for f in tcp["fields"] if f["id"] in ("chksum", "dataofs")] == []
        assert field_in(tcp, "options") == machine(TCP_OPTIONS)
        assert await rebuilt(e, doc) == frame
        rows = field_of(r, "TCP", "options")["options"]["rows"]
        assert [row["name"] for row in rows].count(None) == 2
    with_engine(body)


def test_malformed_options_build_as_typed_with_a_problem() -> None:
    typed = bytes.fromhex("070900")
    problem = "option 7 at byte 0 claims 9 bytes, 3 remain"

    async def body(e: PacketEngine) -> None:
        r = await built(e, with_options("IP", typed))
        frame = base64.b64decode(r["binary"])
        assert frame[14] == 0x46 and frame[34:38] == typed + b"\0"
        opts = field_of(r, "IP", "options")
        assert opts["value"] == machine(typed)
        assert opts["options"]["problem"] == problem and opts["options"]["padded"] == 1
        assert opts["hvalue"] == f"unparsed: 070900 ({problem}), padded to 4 bytes"
    with_engine(body)


def test_hex_not_a_multiple_of_4_is_padded() -> None:
    async def body(e: PacketEngine) -> None:
        r = await built(e, with_options("IP", b"\1\1\1"))
        frame = base64.b64decode(r["binary"])
        assert frame[14] == 0x46 and frame[34:38] == b"\1\1\1\0"
        opts = field_of(r, "IP", "options")
        assert opts["value"] == machine(b"\1\1\1")             # the document's bytes, not the padded ones
        assert [row["name"] for row in opts["options"]["rows"]] == ["NOP"] * 3
        assert opts["options"]["padded"] == 1 and opts["hvalue"].endswith(", padded to 4 bytes")
        tcp = field_of(await built(e, with_options("TCP", b"\1\1\1")), "TCP", "options")
        assert tcp["options"]["padded"] == 1 and tcp["hvalue"].endswith(", padded to 4 bytes")
    with_engine(body)


def test_an_fe_write_past_odd_options_lands_on_its_field() -> None:
    """Review Focus 9: the build pads IP's options before Scapy records a payload's offset."""
    def fe(target: str) -> dict[str, Any]:
        """A 2-byte flow var written at `target`."""
        return {"instructions": [
            {"id": "STLVmFlowVar", "parameters": {"name": "v", "min_value": "1", "max_value": "9",
                                                  "size": "2", "op": "inc"}},
            {"id": "STLVmWrFlowVar", "parameters": {"fv_name": "v", "pkt_offset": target}}],
            "global_parameters": {"cache_size": "10"}}

    async def body(e: PacketEngine) -> None:
        r = await built(e, with_options("IP", b"\1\1\1"), fe("UDP.sport"))
        assert r["field_engine"]["error"] is None
        assert r["field_engine"]["instructions"]["instructions"][1]["pkt_offset"] == 14 + 4 * 6
        r = await built(e, with_options("TCP", b"\1\1\1"), fe("Raw.load"))
        assert r["field_engine"]["error"] is None
        assert r["field_engine"]["instructions"]["instructions"][1]["pkt_offset"] == 14 + 20 + 4 * 6
    with_engine(body)


def test_a_byte_edit_inside_the_options_keeps_the_document_fieldwise() -> None:
    """Review Focus 5: IP, and TCP with an option Scapy rebuilds differently (`1e 01` as `1e 02`)."""
    async def body(e: PacketEngine) -> None:
        doc = await doc_of(e, ROUTER_ALERT_FRAME)
        edited = bytearray(ROUTER_ALERT_FRAME)
        edited[38] = 7
        res = await e.reconstruct_edit(doc, b64(bytes(edited)), {"layer": 1, "field": "options"})
        assert res["warnings"] == []
        assert [layer["id"] for layer in res["doc"]["packet"]] == ["Ether", "IP", "ICMP"]
        assert field_in(res["doc"]["packet"][1], "options") == machine(bytes(edited[34:42]))
        lossy = bytes.fromhex("020405b4" "01" "1e01" "00")
        frame = base64.b64decode((await built(e, with_options("TCP", lossy)))["binary"])
        tdoc = await doc_of(e, frame)
        tcp_edit = bytearray(frame)
        tcp_edit[14 + 20 + 20 + 3] = 0xB5                           # the MSS value's low byte
        res = await e.reconstruct_edit(tdoc, b64(bytes(tcp_edit)), {"layer": 2, "field": "options"})
        assert res["warnings"] == []
        assert field_in(res["doc"]["packet"][2], "options") == machine(bytes(tcp_edit[54:62]))
        assert bytes(tcp_edit[54:62]).hex().endswith("1e0100")     # kept, not rebuilt as 1e 02
    with_engine(body)


def test_field_tree_carries_rows() -> None:
    async def body(e: PacketEngine) -> None:
        opts = field_of(await e.tree(b64(ROUTER_ALERT_FRAME)), "IP", "options")
        assert opts["options"]["rows"][1]["name"] == "Router_Alert"
    with_engine(body)


def test_no_options_is_an_empty_editable_field() -> None:
    async def body(e: PacketEngine) -> None:
        opts = field_of(await built(e, options_model(None)), "IP", "options")
        assert opts["value"] == machine(b"") and opts["options"] == NO_OPTIONS_IP and opts["hvalue"] == "[]"
        tcp = field_of(await built(e, options_model(None, top="TCP")), "TCP", "options")
        assert tcp["value"] == machine(b"") and tcp["options"] == {**NO_OPTIONS_IP, "layer": "TCP"}
    with_engine(body)


def test_a_typed_expression_still_builds() -> None:
    async def body(e: PacketEngine) -> None:
        r = await built(e, options_model(ONE_NOP))
        assert len(base64.b64decode(r["binary"])) == 48
        assert field_of(r, "IP", "options")["value"] == machine(b"\1\0\0\0")      # the wire bytes
    with_engine(body)


def test_no_expression_reaches_the_tree_past_a_class_mismatch() -> None:
    """UDP port 53 dissects as DNS, so the model's next layers stop matching the bytes."""
    udp = {"id": "UDP", "fields": [{"id": "dport", "value": 53}]}
    ip = options_model(ONE_NOP)[1]
    raw = {"id": "Raw", "fields": [{"id": "load", "value": ZZ}]}

    async def body(e: PacketEngine) -> None:
        for model in ([{"id": "Ether"}, {"id": "IP"}, udp, ip, raw],               # the layer is the mismatch
                      [{"id": "Ether"}, {"id": "IP"}, udp, raw, ip, raw]):        # the layer is past it
            r = await built(e, model)
            opts = [f for layer in r["data"] if layer["id"] == "IP" for f in layer["fields"]
                    if f["id"] == "options"]
            assert [f["value"] for f in opts][-1] == machine(b"\1\0\0\0")
            assert not any(f["value"].get("vtype") == "EXPRESSION" for f in opts)
    with_engine(body)


def test_header_lengths_follow_an_options_edit() -> None:
    """Spec goal 5: the options grow to 12 bytes and the header length, total length and checksums follow."""
    async def body(e: PacketEngine) -> None:
        doc = await doc_of(e, ROUTER_ALERT_FRAME)
        doc["packet"][1]["fields"] = [{"id": "options", "value": machine(PADDED_12)}]
        frame = await rebuilt(e, doc)
        assert frame[14] == 0x48 and inet_sum(frame[14:46]) == 0
        assert struct.unpack("!H", frame[16:18])[0] == len(frame) - 14
        tcp = base64.b64decode((await built(e, with_options("TCP", bytes(12))))["binary"])
        segment = tcp[34:]
        assert segment[12] >> 4 == 8
        pseudo = tcp[26:34] + struct.pack("!BBH", 0, 6, len(segment))
        assert inet_sum(pseudo + segment) == 0
    with_engine(body)


def test_a_bytes_expression_for_ip_options_still_builds() -> None:
    """An EXPRESSION that evaluates to bytes (or a list of them) is no Packet item: the service builds it, so
    does the worker, and the tree shows the wire bytes as MACHINE."""
    async def body(e: PacketEngine) -> None:
        for expr in ("b'\\x01\\x01\\x01\\x01'", "[b'\\x01\\x01\\x01\\x01']"):
            r = await built(e, options_model(expr))
            frame = base64.b64decode(r["binary"])
            assert frame[14] == 0x46 and frame[34:38] == b"\1\1\1\1"
            assert field_of(r, "IP", "options")["value"] == machine(b"\1\1\1\1")
    with_engine(body)


def ra_rows() -> list[dict[str, Any]]:
    """The rows of the Router Alert frame's option bytes, as the field tree shows them."""
    from trex_web_gui.trexapi.loader import load
    from trex_web_gui.trexapi.packet_options import decode
    load()                                  # the codec's kind table reads Scapy's classes
    return list(decode("IP", RA)["rows"])


def test_definitions_carry_option_kinds() -> None:
    async def body(e: PacketEngine) -> None:
        kinds = (await e.definitions())["optionKinds"]
        assert "Router_Alert" in [k["name"] for k in kinds["IP"]]
        assert "MSS" in [k["name"] for k in kinds["TCP"]]
    with_engine(body)


def test_option_kinds_are_part_of_the_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The etag hashes the definitions with `optionKinds` in them. The monkeypatch reaches an in-process
    worker Engine only, not PacketEngine's subprocess."""
    from trex_web_gui.trexapi import packet_options
    first = engine_worker().definitions({})["etag"]
    real = packet_options.option_kinds
    monkeypatch.setattr(packet_options, "option_kinds",
                        lambda layer: [*real(layer), {"name": "Extra", "number": 99, "fields": []}])
    changed = engine_worker().definitions({})
    assert changed["etag"] != first
    assert changed["optionKinds"]["IP"][-1]["name"] == "Extra"


def test_encode_options_answers_the_bytes() -> None:
    async def body(e: PacketEngine) -> None:
        got = await e.encode_options("IP", ra_rows(), 2, 6)
        assert base64.b64decode(got) == RA
        added = [{"name": "RR", "number": 7, "fields": {"pointer": 4, "routers": ["16.0.0.1"]}}]
        got_added = base64.b64decode(await e.encode_options("IP", added, 0, 0))
        assert got_added == bytes.fromhex("07070410000001") + b"\0"
    with_engine(body)


def test_encode_options_refusals_carry_the_codec_text() -> None:
    """The op's two `except` clauses: each codec refusal becomes OptionsRefused with its own code."""
    async def body(e: PacketEngine) -> None:
        nops = [{"name": "NOP", "number": 1, "fields": {}}] * 41
        with pytest.raises(OptionsRefused) as long:
            await e.encode_options("IP", nops, 0, 0)
        assert long.value.code == "options_too_long"
        assert str(long.value) == "options are 41 bytes; the header holds 40 at most"
        with pytest.raises(OptionsRefused) as bad:
            await e.encode_options("IP", [{"name": "Bogus", "number": 1, "fields": {}}], 0, 0)
        assert bad.value.code == "bad_option" and str(bad.value) == "'Bogus' is not a known IP option"
        assert not issubclass(OptionsRefused, PacketError)
        assert e.starts == 1                                    # a refusal kills no worker
    with_engine(body)
