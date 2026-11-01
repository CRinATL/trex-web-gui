"""The fake's own port binding: port 0 binds a free port itself, an explicit port is bound as given."""
import re
import subprocess
import sys
from collections.abc import Sequence

import zmq
from support import FAKE, FIXTURES, free_port_pair
from test_fake_delay import REQ, sock

READY = re.compile(r"\[fake\] ready rpc=(\d+) pub=(\d+) ")


def start(rpc: int, pub: int) -> tuple[subprocess.Popen, re.Match]:
    """Spawn the fake on `rpc` and `pub`; return it with the match of its ready line (the caller kills it)."""
    argv: Sequence[str] = [sys.executable, "-I", "-B", str(FAKE), str(FIXTURES), str(rpc), str(pub)]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ready = proc.stdout.readline() if proc.stdout else ""
    m = READY.match(ready)
    if not m:
        proc.kill()
        raise AssertionError(f"fake did not start: {ready + proc.communicate(timeout=5)[0]!r}")
    return proc, m


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
    """Explicit ports: the fake binds exactly the ports given."""
    rpc, pub = free_port_pair()
    proc, m = start(rpc, pub)
    try:
        assert (int(m[1]), int(m[2])) == (rpc, pub)
    finally:
        proc.kill()
        proc.communicate(timeout=5)
