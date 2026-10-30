"""Poll until TRex answers api_sync_v2 + get_version via STLClient; print seconds since t0 file.

Usage: ready.py <t0-file>   (run with the venv python so zmq and yaml import)
Imports the vendored client from backend/trex_web_gui/_vendor, with TREX_EXT_LIBS set.
"""
import os
import sys
import time

V = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "trex_web_gui", "_vendor")
os.environ.setdefault("TREX_EXT_LIBS", os.path.join(V, "external_libs"))
os.environ.setdefault("TREX_SYSTEM_LIBS", "zmq,yaml")
sys.path.insert(0, V)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guestenv

SYNC_PORT, ASYNC_PORT = guestenv.ports()
t0 = float(open(sys.argv[1]).read())
from trex.stl.api import STLClient

while time.time() - t0 < 120:
    c = STLClient(server="127.0.0.1", sync_port=SYNC_PORT, async_port=ASYNC_PORT,
                  verbose_level="none", sync_timeout=1)
    try:
        c.connect()
        v = c.get_server_version()["version"]
        c.disconnect()
        print(f"RPC ready (connect+get_version OK, {v}) {time.time() - t0:.2f}s after launch")
        sys.exit(0)
    except Exception:
        time.sleep(0.2)
print("NOT READY after 120s")
sys.exit(1)
