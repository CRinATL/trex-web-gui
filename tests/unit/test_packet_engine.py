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
from trex_web_gui.studio.packetdoc import PacketDoc, model_of, parse_doc
from trex_web_gui.trexapi import packet
from trex_web_gui.trexapi.packet import PacketEngine, PacketError

PROTOCOL_KEYS = ["fields", "id", "name"]
FIELD_KEYS = ["auto", "bits", "id", "max", "min", "name", "regex", "type", "values_dict"]
LAYER_KEYS = ["fields", "id", "offset", "real_id", "valid_structure"]
FIELD_RESULT_KEYS = ["hvalue", "id", "length", "offset", "value"]

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
            if name == "tcp_options":
                assert exprs and res["warnings"] == []
            else:
                assert not exprs and len(res["warnings"]) == 1
                assert "cannot be edited as a field here; from this layer on the packet is raw bytes" \
                    in res["warnings"][0]
        ip = await e.doc_from_binary(b64(bytes.fromhex(FRAMES["ip_options"])), None)
        assert ip["warnings"][0].startswith("layer 1 IP: options ")
        assert [layer["id"] for layer in ip["doc"]["packet"]] == ["Ether", "Raw"]
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
        assert res["warnings"][0].startswith("the byte edit re-derived")
        assert any("raw bytes" in w for w in res["warnings"])
        for n in walk(res["doc"]["packet"]):
            if isinstance(n, dict) and n.get("vtype") == "EXPRESSION":
                ast.literal_eval(n["expr"])
        assert await rebuilt(e, res["doc"]) == bytes(raw)
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
        assert packet_worker.FALLBACK_WARNING in zres["warnings"]
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

def test_tree_op_matches_the_backend_tree() -> None:
    from trex_web_gui.trexapi import decode
    raw = udp_record(200)
    res = engine_worker().tree({"binary": b64(raw)})
    assert res.pop("ok") is True
    assert res == decode.tree(raw)


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
    raw = IPV4_ETHER.ljust(30_000, b"\x00")
    res = eng.tree({"binary": b64(raw)})
    assert given and all(n <= packet_worker.EDITOR_DECODE_BYTES for n in given)
    assert base64.b64decode(res["binary"]) == raw
    assert res["data"][-1]["id"] == "Raw"


def test_tree_op_refuses_a_frame_shorter_than_ethernet() -> None:
    async def body(e: PacketEngine) -> None:
        with pytest.raises(PacketError) as info:
            await e.tree(b64(bytes(5)))
        assert not info.value.restarted and "error" in str(info.value)
    with_engine(body)
