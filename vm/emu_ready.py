"""Wait until the guest's EMU server answers `api_sync_v2` and `ping` on its forwarded port.

Usage: emu_ready.py [--timeout S]   (run with the venv python so zmq imports; default 60 s)
Connects to 127.0.0.1:<EMU port> (vm/guestenv.py) with the backend's RawRpc: api_sync_v2 for EMU 1.1, then
`ping` with the returned api_h. Retries until both succeed (exit 0, printing `emu ready in <s> s`) or
the timeout passes (exit 1)."""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
sys.path.insert(0, HERE)
import guestenv  # noqa: E402
from trex_web_gui.trexapi.errors import TrexApiError  # noqa: E402
from trex_web_gui.trexapi.rawrpc import RawRpc  # noqa: E402

EMU_API = ("EMU", 1, 1)


def attempt(port: int) -> bool:
    """One handshake and ping against 127.0.0.1:port; False when EMU does not answer or refuses."""
    rpc = RawRpc("127.0.0.1", port, 2.0)
    try:
        rpc.api_sync(*EMU_API)
        rpc.call("ping")
        return True
    except TrexApiError:
        return False
    finally:
        rpc.close()


def main() -> int:
    """Retry `attempt` until it succeeds or --timeout seconds pass; returns the exit code."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=60.0)
    timeout = ap.parse_args().timeout
    port = guestenv.emu_port()
    t0 = time.monotonic()
    while True:
        if attempt(port):
            print(f"emu ready in {time.monotonic() - t0:.1f} s")
            return 0
        if time.monotonic() - t0 >= timeout:
            break
        time.sleep(0.5)
    print(f"no EMU answer within {timeout:g} s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
