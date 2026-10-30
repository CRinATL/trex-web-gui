"""The fake's --pub-replay (spec §8): the recorded jammy frames, looping, with the RPC side unchanged."""
import base64
import json
import signal
import time

import zmq
from support import FIXTURES
from trex_web_gui.monitor.wire import decode_frame

STL = FIXTURES / "p5" / "pub-stl.jsonl"


def _names() -> set[str]:
    """Every frame name in pub-stl.jsonl."""
    out = set()
    for line in STL.read_text().splitlines():
        rec = json.loads(line)
        f = decode_frame(base64.b64decode(rec["frame"])) if rec else None
        if f:
            out.add(f.name)
    return out


def _loop_seconds() -> float:
    """Roughly the replay loop length: the sum of each phase's last t, plus the join gaps."""
    last: dict[str, float] = {}
    for line in STL.read_text().splitlines():
        rec = json.loads(line)
        if rec:
            last[rec["phase"]] = rec["t"]
    return sum(last.values()) + 0.5 * len(last)


def test_replay_loops_the_recorded_names(fake_trex):
    fake = fake_trex("--pub-replay", str(STL))
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    sub.connect(f"tcp://127.0.0.1:{fake.pub_port}")
    want, seen = _names(), set()
    deadline = time.monotonic() + 1.5 * _loop_seconds()
    try:
        while time.monotonic() < deadline and not want <= seen:
            if sub.poll(200):
                f = decode_frame(sub.recv())
                if f:
                    seen.add(f.name)
    finally:
        sub.close(0)
        ctx.term()
    assert want <= seen


def test_rpc_still_answers_while_replaying(fake_trex):
    fake = fake_trex("--pub-replay", str(STL))
    ctx = zmq.Context()
    req = ctx.socket(zmq.REQ)
    req.connect(f"tcp://127.0.0.1:{fake.rpc_port}")
    try:
        def call(method, **params):
            """One JSON-RPC round trip on the REQ socket."""
            req.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode())
            assert req.poll(5000)
            return json.loads(req.recv())
        api_h = call("api_sync_v2", name="STL", major=5, minor=1, user="t")["result"]["api_h"]
        resp = call("get_version", api_h=api_h)
    finally:
        req.close(0)
        ctx.term()
    assert resp["result"]["version"] == "v3.08"


def test_synthetic_flags_send_on_their_own_timer(fake_trex):
    fake = fake_trex("--pub-big", "5000", "--pub-names", "3", "--pub-garbage")
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    sub.connect(f"tcp://127.0.0.1:{fake.pub_port}")
    lens, names, deadline = set(), set(), time.monotonic() + 5
    try:
        while time.monotonic() < deadline:
            if sub.poll(200):
                raw = sub.recv()
                lens.add(len(raw))
                try:
                    f = decode_frame(raw)
                except Exception:   # the garbage frames
                    continue
                if f:
                    names.add(f.name)
    finally:
        sub.close(0)
        ctx.term()
    assert 5000 in lens
    assert {"big", "x-0", "x-2"} <= names


def test_replay_does_not_burst_after_a_stall(fake_trex):
    """SIGSTOP for 3 s then SIGCONT: the replay carries on from the next frame at its pace instead of sending
    every overdue frame at once."""
    fake = fake_trex("--pub-replay", str(STL))
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    sub.connect(f"tcp://127.0.0.1:{fake.pub_port}")
    try:
        assert sub.poll(3000)
        sub.recv()
        fake.proc.send_signal(signal.SIGSTOP)
        time.sleep(3.0)
        while sub.poll(0):
            sub.recv()                      # whatever was in flight before the stop
        fake.proc.send_signal(signal.SIGCONT)
        got, end = 0, time.monotonic() + 0.4
        while time.monotonic() < end:
            if sub.poll(50):
                sub.recv()
                got += 1
    finally:
        sub.close(0)
        ctx.term()
    assert 1 <= got <= 2, got
