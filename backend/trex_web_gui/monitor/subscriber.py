"""The Monitor's passive PUB listener: one SUB socket, no RPC socket, a 2 s silence watchdog."""
import asyncio
import contextlib
import logging
import time
from collections import Counter
from collections.abc import Callable

import zmq
import zmq.asyncio
from zmq.constants import Event, PollEvent, SocketOption, SocketType

from .wire import MAXMSGSIZE, Frame, Undecodable, decode_frame

STALE_AFTER_S = 2.0
POLL_MS = 250
REOPEN_MIN_S = 0.1          # wait before the first reopen of a dropped link; doubles to REOPEN_MAX_S
REOPEN_MAX_S = 2.0
log = logging.getLogger(__name__)


class MonitorSubscriber:
    """Subscribes to every PUB message of one TRex and hands each decoded frame to `on_frame`. `on_state`
    gets "connecting" at start, "connected" on the first message and again after silence, and "stale" once
    STALE_AFTER_S pass without a message. ZMQ reconnects by itself, so a restarted TRex is simply heard
    again. Both callbacks run on the loop; none runs after `stop()` returns."""

    def __init__(self, host: str, port: int, on_frame: Callable[[Frame], None],
                 on_state: Callable[[str], None]) -> None:
        """A subscriber that has not started; `start()` opens the socket."""
        self.host, self.port = host, port
        self.dropped_triggers = 0
        self.reopens = 0                                # sockets reopened after a DISCONNECTED event
        self.undecodable: Counter[str] = Counter()      # Undecodable.reason -> count
        self._on_frame, self._on_state = on_frame, on_state
        self._ctx: zmq.asyncio.Context | None = None
        self._sock: zmq.asyncio.Socket | None = None
        self._mon: zmq.asyncio.Socket | None = None
        self._task: asyncio.Task[None] | None = None

    def _open(self) -> None:
        """Open a SUB socket and its disconnect monitor on the context, and connect. SUBSCRIBE is the empty
        prefix, LINGER 0 and MAXMSGSIZE bound what libzmq will take; an IPv6 literal turns IPV6 on and is
        bracketed in the endpoint."""
        assert self._ctx is not None
        sock = self._ctx.socket(SocketType.SUB)
        self._sock = sock
        sock.setsockopt(SocketOption.SUBSCRIBE, b"")
        sock.setsockopt(SocketOption.LINGER, 0)
        sock.setsockopt(SocketOption.MAXMSGSIZE, MAXMSGSIZE)
        host = self.host
        if ":" in host:
            sock.setsockopt(SocketOption.IPV6, 1)
            host = f"[{host}]"
        self._mon = sock.get_monitor_socket(Event.DISCONNECTED)
        sock.connect(f"tcp://{host}:{self.port}")

    def _close_socket(self) -> None:
        """Close the socket and its monitor (not the context)."""
        sock, mon, self._sock, self._mon = self._sock, self._mon, None, None
        if sock is not None:
            sock.close(0)
        if mon is not None:
            mon.close(0)

    def start(self) -> None:
        """Make a context of its own (never Context.instance()), open the socket and start the task. Must
        run on the loop."""
        self._ctx = zmq.asyncio.Context()
        try:
            self._open()
        except zmq.error.ZMQError:        # an endpoint libzmq refuses: leave nothing open
            self._close_socket()
            ctx, self._ctx = self._ctx, None
            ctx.term()
            raise
        self._task = asyncio.get_running_loop().create_task(self._run())
        self._task.add_done_callback(self._on_done)

    async def stop(self) -> None:
        """Cancel the task, await it, then close the socket and terminate the context on the loop."""
        task, ctx = self._task, self._ctx
        self._task = self._ctx = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):    # a death was logged by _on_done
                await task
        self._close_socket()
        if ctx is not None:
            ctx.term()

    def _on_done(self, task: asyncio.Task[None]) -> None:
        """An unexpected end of the poll task is logged and reported as "stale", never left as "connected"."""
        if task.cancelled() or task.exception() is None:
            return
        log.error("monitor subscriber %s:%s died", self.host, self.port, exc_info=task.exception())
        self._on_state("stale")

    def _decode(self, raw: bytes) -> None:
        """Count a trigger or an undecodable message; pass any other frame to on_frame."""
        try:
            frame = decode_frame(raw)
        except Undecodable as e:
            self.undecodable[e.reason] += 1
            return
        if frame is None:
            self.dropped_triggers += 1
            return
        try:
            self._on_frame(frame)
        except Exception:
            log.exception("monitor frame %r refused by its handler", frame.name[:64])
            self.undecodable["shape"] += 1

    async def _run(self) -> None:
        """Poll in POLL_MS steps, reading one message per ready poll; any message counts as the publisher
        being alive (a trigger or a garbage frame too), STALE_AFTER_S without one is "stale". A
        DISCONNECTED event reopens the socket: libzmq reconnects by itself after a lost link, but not after
        a protocol error such as a message over MAXMSGSIZE, which would otherwise leave the Monitor deaf for
        good."""
        state, last = "connecting", time.monotonic()
        self._on_state(state)
        poller = zmq.asyncio.Poller()
        backoff, reopen_at = REOPEN_MIN_S, None     # reopen_at: when the socket is reopened; None = open
        while True:
            now = time.monotonic()
            if reopen_at is not None and now >= reopen_at:
                reopen_at = None
                self._open()
                self.reopens += 1
            wait_ms = POLL_MS       # a step of POLL_MS, shortened so "stale" lands at STALE_AFTER_S exactly
            if state == "connected":
                wait_ms = min(POLL_MS, max(1, int((last + STALE_AFTER_S - now) * 1000) + 1))
            sock, mon, ready = self._sock, self._mon, {}
            if reopen_at is not None:                   # no socket yet: wait out the backoff
                await asyncio.sleep(min(wait_ms / 1000, max(0.0, reopen_at - now)))
            else:
                assert sock is not None and mon is not None
                poller.register(sock, PollEvent.POLLIN)
                poller.register(mon, PollEvent.POLLIN)
                try:
                    ready = dict(await poller.poll(wait_ms))
                finally:
                    poller.unregister(sock)
                    poller.unregister(mon)
            if mon is not None and mon in ready:
                await mon.recv_multipart()
                self._close_socket()
                reopen_at = time.monotonic() + backoff
                backoff = min(backoff * 2, REOPEN_MAX_S)
                continue
            if sock is not None and sock in ready:
                last, backoff = time.monotonic(), REOPEN_MIN_S
                if state != "connected":
                    state = "connected"
                    self._on_state(state)
                self._decode(await sock.recv())
            elif state == "connected" and time.monotonic() - last >= STALE_AFTER_S:
                state = "stale"
                self._on_state(state)
