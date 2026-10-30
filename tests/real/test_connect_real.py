import asyncio
import os
import socket
import time

import pytest
from support import real_ports
from trex_web_gui.sessions import ConnectParams, TrexSession
from trex_web_gui.trexapi import loss_bound

pytestmark = pytest.mark.real
HOST = os.environ.get("TREX_REAL_HOST", "")
MON = os.environ.get("TREX_VM_MONITOR", "")


def monitor(cmd: str) -> None:
    """Send one QEMU monitor command and wait for (and discard) its reply, so it has taken effect."""
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(5.0)
        sock.connect(MON)
        sock.recv(4096)                                   # greeting banner
        sock.sendall(cmd.encode() + b"\n")
        sock.recv(4096)                                   # echo / reply


@pytest.fixture(autouse=True)
def need_vm() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST and TREX_VM_MONITOR are set
    (vm/real.sh sets both)."""
    if not HOST or not MON:
        pytest.skip("run via make check-real")
    real_ports()


# loss wait (loss_bound with margin 3) + two connects, the resume pause and sleeps, with headroom
@pytest.mark.timeout(loss_bound(3.0, margin=3.0) + 45)
def test_real_connect_info_resumed_then_timeout() -> None:
    async def run() -> None:
        msgs: list[dict] = []
        s = TrexSession(msgs.append)
        try:
            t = time.monotonic()
            info = await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            print(f"connect_s={time.monotonic() - t:.2f}")
            assert (info.version, info.mode, info.port_count) == ("v3.08", "STL", 2)
            assert {p.get("driver") for p in info.ports} == {"net_af_packet"}
            await asyncio.sleep(1.0)
            assert not any(m.get("name") == "subscriber resumed" for m in msgs)    # not news at connect
            await asyncio.to_thread(monitor, "stop")                # freeze the guest: the server goes silent
            try:
                t = time.monotonic()
                while s.snapshot()["state"] != "lost":
                    assert time.monotonic() - t < loss_bound(3.0, margin=3.0), "no loss detected"
                    await asyncio.sleep(0.1)
                print(f"lost_after_s={time.monotonic() - t:.2f}")
                # the reader's subscriber can mark the session lost first (it publishes no event); the control
                # client's own "subscriber timeout" event follows
                t = time.monotonic()
                while not any(m.get("name") == "subscriber timeout" for m in msgs):
                    assert time.monotonic() - t < 3.0, "no 'subscriber timeout' event after the loss"
                    await asyncio.sleep(0.05)
                print(f"event_after_loss_s={time.monotonic() - t:.2f}")
            finally:
                await asyncio.to_thread(monitor, "cont")
            await asyncio.sleep(1.0)
            info2 = await s.connect(ConnectParams(HOST, *real_ports(), 3.0))     # recovers after the pause
            assert info2.version == "v3.08"
        finally:
            await s.disconnect()
            await s.close()
    asyncio.run(run())
