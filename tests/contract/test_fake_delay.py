"""The fake's --delay-ms / --jitter-ms (P3b follow-up 4, Task 1): a remote backend, in-process."""
import json
import time

import zmq

REQ = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "get_version", "params": {}}).encode()


def sock(kind: int, port: int, ctx: zmq.Context) -> zmq.Socket:
    """A zmq socket of type `kind` on `ctx` connected to 127.0.0.1:`port`, with LINGER 0; closed when the
    caller destroys `ctx`."""
    s = ctx.socket(kind)
    s.setsockopt(zmq.LINGER, 0)
    s.connect(f"tcp://127.0.0.1:{port}")
    return s


def test_delay_adds_to_each_round_trip(fake_trex) -> None:
    f = fake_trex("--delay-ms", "100")
    ctx = zmq.Context()
    try:
        s = sock(zmq.REQ, f.rpc_port, ctx)
        times = []
        for _ in range(5):
            t = time.monotonic()
            s.send(REQ)
            assert s.poll(2000)
            s.recv()
            times.append(time.monotonic() - t)
        print("round_trips_s=" + ",".join(f"{x:.3f}" for x in times))
        assert all(0.100 <= x < 0.250 for x in times)
    finally:
        ctx.destroy()


def test_delay_does_not_serialise_two_clients(fake_trex) -> None:
    f = fake_trex("--delay-ms", "200")
    ctx = zmq.Context()
    try:
        a, b = sock(zmq.REQ, f.rpc_port, ctx), sock(zmq.REQ, f.rpc_port, ctx)
        t = time.monotonic()
        a.send(REQ)
        b.send(REQ)
        got = []
        for s in (a, b):
            assert s.poll(2000)
            s.recv()
            got.append(time.monotonic() - t)
        print("two_clients_s=" + ",".join(f"{x:.3f}" for x in got))
        assert all(0.200 <= x < 0.350 for x in got)     # serialised, the second would take >= 0.400
    finally:
        ctx.destroy()


def test_jitter_keeps_order_per_peer(fake_trex) -> None:
    f = fake_trex("--delay-ms", "50", "--jitter-ms", "40")
    ctx = zmq.Context()
    try:
        d = sock(zmq.DEALER, f.rpc_port, ctx)
        for i in range(1, 21):
            d.send_multipart(
                [b"", json.dumps({"jsonrpc": "2.0", "id": i, "method": "get_version", "params": {}}).encode()]
            )
        ids = []
        for _ in range(20):
            assert d.poll(2000)
            ids.append(json.loads(d.recv_multipart()[-1])["id"])
        print(f"jitter_ids={ids}")
        assert ids == list(range(1, 21))
    finally:
        ctx.destroy()


def publish_now_to_trigger(f) -> float:
    """Seconds from sending publish_now (a request reaches the fake at once) to the first '{}' trigger after
    it."""
    ctx = zmq.Context()
    try:
        sub = sock(zmq.SUB, f.pub_port, ctx)
        sub.setsockopt(zmq.SUBSCRIBE, b"")
        req = sock(zmq.REQ, f.rpc_port, ctx)
        req.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "api_sync_v2",
                             "params": {"name": "STL", "major": 5, "minor": 1}}).encode())
        assert req.poll(2000)
        api_h = json.loads(req.recv())["result"]["api_h"]    # the fake refuses publish_now without one
        end = time.monotonic() + 3
        while time.monotonic() < end:                    # wait for a global (not a '{}')
            if sub.poll(100) and sub.recv() != b"{}":
                break
        else:
            raise AssertionError("no global message within 3 s")
        # the '{}' that follows the global lands just after it
        while sub.poll(40):
            sub.recv()
        t_send = time.monotonic()
        req.send(
            json.dumps(
                {"jsonrpc": "2.0", "id": 2, "method": "publish_now", "params": {"api_h": api_h}}
            ).encode()
        )
        # the periodic '{}' is ~0.5 s after the global, past this window
        while time.monotonic() - t_send < 1.0:
            if sub.poll(10) and sub.recv() == b"{}":
                return time.monotonic() - t_send
        raise AssertionError("no '{}' trigger within 1 s of publish_now")
    finally:
        ctx.destroy()


def test_publish_is_delayed(fake_trex) -> None:
    dt = publish_now_to_trigger(fake_trex("--delay-ms", "150"))
    print(f"publish_now_to_trigger_s={dt:.3f}")
    assert 0.150 <= dt < 0.250


def test_publish_now_trigger_is_immediate_without_delay(fake_trex) -> None:
    dt = publish_now_to_trigger(fake_trex())
    print(f"publish_now_to_trigger_nodelay_s={dt:.3f}")
    assert dt < 0.050


def test_zero_delay_answers_at_once(fake_trex) -> None:
    f = fake_trex()
    ctx = zmq.Context()
    try:
        s = sock(zmq.REQ, f.rpc_port, ctx)
        t = time.monotonic()
        s.send(REQ)
        assert s.poll(2000)
        s.recv()
        rt = time.monotonic() - t
        print(f"zero_delay_round_trip_s={rt:.3f}")
        assert rt < 0.050
    finally:
        ctx.destroy()
