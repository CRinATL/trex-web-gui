"""Wait until the guest's TRex publishes a `trex-global` message on its async (PUB) port.

Usage: pub_ready.py [--timeout S]   (run with the venv python so zmq imports; default 60 s)
Subscribes to 127.0.0.1:<async port> (vm/guestenv.py) and decodes each frame with the vendored ZippedMsg
(a frame that is not zipped is read as plain JSON). Exits 0 on the first `trex-global`, printing
`pub ready in <s> s`; exits 1 at the timeout."""
import argparse
import json
import os
import sys
import time
import zlib

V = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "trex_web_gui", "_vendor")
os.environ.setdefault("TREX_EXT_LIBS", os.path.join(V, "external_libs"))
os.environ.setdefault("TREX_SYSTEM_LIBS", "zmq,yaml")
sys.path.insert(0, V)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guestenv  # noqa: E402
import zmq  # noqa: E402
from trex.utils.zipmsg import ZippedMsg  # noqa: E402


def frame_name(raw: bytes) -> str | None:
    """The `name` of one PUB frame, or None when it is corrupt or not a JSON object with a string name."""
    try:
        body = ZippedMsg().decompress(raw) or raw
    except zlib.error:   # the magic is right and the zipped body is corrupt
        return None
    try:
        msg = json.loads(body)
    except ValueError:
        return None
    name = msg.get("name") if isinstance(msg, dict) else None
    return name if isinstance(name, str) else None


def main() -> int:
    """Poll the PUB socket until `trex-global` arrives or --timeout seconds pass; returns the exit code."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=60.0)
    timeout = ap.parse_args().timeout
    _, async_port = guestenv.ports()
    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(f"tcp://127.0.0.1:{async_port}")
    t0 = time.monotonic()
    try:
        while (left := timeout - (time.monotonic() - t0)) > 0:
            if not sock.poll(int(min(left, 0.5) * 1000)):
                continue
            if frame_name(sock.recv()) == "trex-global":
                print(f"pub ready in {time.monotonic() - t0:.1f} s")
                return 0
    finally:
        sock.close(linger=0)
        ctx.term()
    print(f"no trex-global within {timeout:g} s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
