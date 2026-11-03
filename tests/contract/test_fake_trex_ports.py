"""The fake's own port binding: port 0 binds a free port itself, an explicit port is bound as given."""
import re
import subprocess
import sys

import zmq
from support import FAKE, FAKE_READY, FIXTURES, free_port_pair, spawn_ready
from test_fake_delay import REQ, sock


def start(rpc: int, pub: int) -> tuple[subprocess.Popen[str], re.Match[str]]:
    """Spawn the fake on `rpc` and `pub`; return it with the match of its ready line (the caller kills it)."""
    return spawn_ready([sys.executable, "-I", "-B", str(FAKE), str(FIXTURES), str(rpc), str(pub)], FAKE_READY,
                       "fake")


def test_the_fake_binds_its_own_ports_when_given_zero() -> None:
    """Port 0: the fake binds and reports ephemeral ports, and answers on them."""
    proc, m = start(0, 0)
    ctx = zmq.Context()
    try:
        rpc, pub = int(m[1]), int(m[2])
        assert rpc != 0 and pub != 0 and rpc != pub
        s = sock(zmq.REQ, rpc, ctx)
        s.send(REQ)
        assert s.poll(2000)
        s.recv()
    finally:
        ctx.destroy()
        proc.kill()
        proc.communicate(timeout=5)


def test_the_fake_binds_explicit_ports_as_given() -> None:
    """Explicit ports: the fake binds exactly the ports given. Choosing them is racy by nature (another xdist
    worker can take one first), so a start that fails on a taken port is retried with a fresh pair."""
    for attempt in range(3):
        rpc, pub = free_port_pair()
        try:
            proc, m = start(rpc, pub)
            break
        except RuntimeError as e:
            if "Address already in use" not in str(e) or attempt == 2:
                raise
    try:
        assert (int(m[1]), int(m[2])) == (rpc, pub)
    finally:
        proc.kill()
        proc.communicate(timeout=5)
