"""The Monitor's state: the latest value of each known PUB name, a bounded inspector of the rest, and a
`monitor` message coalesced to at most 2 Hz."""
import asyncio
import json
import time
from collections.abc import Callable

import zmq

from trex_web_gui.sessions.trex_session import Publish

from .subscriber import MonitorSubscriber
from .wire import Frame

PUBLISH_EVERY_S = 0.5
OTHER_MAX = 20
NAME_MAX_BYTES = 256
OTHER_DATA_BYTES = 65536
KNOWN_MAX_BYTES = 1048576
KNOWN = {"trex-global": "global", "trex-latecny": "latency", "trex-latecny-v2": "latency_v2",
         "rx-check": "rx_check", "template_info": "templates"}


class MonitorHostRefused(ValueError):
    """libzmq refused the target's endpoint; the Monitor is left disconnected."""


class MonitorSession:
    """Owns at most one MonitorSubscriber and the data it delivers. All methods run on the loop. Known names
    have slots of their own, so unknown names can never starve them; unknown names are capped at OTHER_MAX
    entries of OTHER_DATA_BYTES each. `clock` paces the publishing, `wall` stamps the data (both
    injectable)."""

    def __init__(self, publish: Publish, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        """A disconnected session that has published nothing."""
        self._publish, self._clock, self._wall = publish, clock, wall
        self._sub: MonitorSubscriber | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._last_pub = -PUBLISH_EVERY_S
        self._dirty = False
        self._state = "disconnected"
        self._target: dict[str, object] | None = None
        self._reset()

    def _reset(self) -> None:
        """Forget every slot and counter."""
        self._frames = self._bytes = self._oversize = self._other_names = self._long_names = 0
        self._ts: float | None = None
        self._stale_since: float | None = None
        self._known: dict[str, object] = {}                 # slot -> data
        self._known_ts: dict[str, float] = {}               # PUB name -> ts
        self._other: dict[str, dict[str, object]] = {}

    # -- public -------------------------------------------------------------------------------
    async def connect(self, host: str, port: int) -> dict[str, object]:
        """Stop any running subscriber, clear every slot, publish "connecting" and start a subscriber on
        host:port. MonitorHostRefused (a ValueError) when libzmq refuses the endpoint; the state is then
        "disconnected" with no target."""
        await self._stop_sub()
        self._reset()
        self._target = {"host": host, "port": port}
        self._state = "connecting"
        self._flush()
        sub = MonitorSubscriber(host, port, self.on_frame, self.on_state)
        try:
            sub.start()
        except zmq.error.ZMQError as e:
            self._target = None
            self._state = "disconnected"
            self._flush()
            raise MonitorHostRefused(f"cannot subscribe to {host}:{port}: {e}") from None
        self._sub = sub
        return self.snapshot()

    async def disconnect(self) -> dict[str, object]:
        """Stop the subscriber, clear every slot and publish "disconnected"."""
        await self._stop_sub()
        self._reset()
        self._target = None
        self._state = "disconnected"
        self._flush()
        return self.snapshot()

    async def close(self) -> None:
        """Stop the subscriber and the publish timer without publishing."""
        await self._stop_sub()
        self._cancel_timer()

    def snapshot(self) -> dict[str, object]:
        """The `monitor` message. Publishes nothing."""
        sub = self._sub
        undecodable = sum(sub.undecodable.values()) - sub.undecodable["oversize"] if sub else 0
        return {
            "type": "monitor", "state": self._state, "target": self._target,
            "alive": self._state == "connected", "frames": self._frames, "bytes": self._bytes,
            "dropped_triggers": sub.dropped_triggers if sub else 0,
            "dropped": {"oversize": self._oversize + (sub.undecodable["oversize"] if sub else 0),
                        "undecodable": undecodable, "other_names": self._other_names,
                        "long_name": self._long_names},
            "ts": self._ts, "stale_since": self._stale_since,
            **{slot: self._known.get(slot) for slot in KNOWN.values()},
            "known_ts": dict(self._known_ts), "other": {k: dict(v) for k, v in self._other.items()},
        }

    # -- subscriber callbacks -------------------------------------------------------------------
    def on_frame(self, frame: Frame) -> None:
        """Take one decoded frame: a known name replaces its slot (kept as it was when over KNOWN_MAX_BYTES),
        any other name goes to the capped inspector. Publishes when due."""
        if len(frame.name.encode()) > NAME_MAX_BYTES:       # never stored: it would ride in every message
            self._long_names += 1
            return
        self._frames += 1
        self._bytes += frame.wire_len
        now = self._wall()
        self._ts = now
        text = json.dumps(frame.data)
        size = len(text.encode())
        slot = KNOWN.get(frame.name)
        if slot is not None:
            if size > KNOWN_MAX_BYTES:
                self._oversize += 1
            else:
                self._known[slot] = frame.data
                self._known_ts[frame.name] = now
        elif frame.name in self._other or len(self._other) < OTHER_MAX:
            if size > OTHER_DATA_BYTES:
                self._other[frame.name] = {"ts": now, "data_text": text[:OTHER_DATA_BYTES], "truncated": True,
                                           "bytes": size}
            else:
                self._other[frame.name] = {"ts": now, "data": frame.data}
        else:
            self._other_names += 1
        self._dirty = True
        self._publish_if_due()

    def on_state(self, state: str) -> None:
        """Take a state from the subscriber; a change publishes at once."""
        if state == self._state:
            return
        self._state = state
        self._stale_since = self._wall() if state == "stale" else None
        self._flush()

    # -- publishing -----------------------------------------------------------------------------
    def _flush(self) -> None:
        """Publish now and restart the pacing."""
        self._cancel_timer()
        self._dirty = False
        self._last_pub = self._clock()
        self._publish(self.snapshot())

    def _publish_if_due(self) -> None:
        """Publish when PUBLISH_EVERY_S have passed since the last one; otherwise arrange a publish for then
        (when a loop runs), so the last frames of a burst are not left waiting for the next one."""
        wait = self._last_pub + PUBLISH_EVERY_S - self._clock()
        if wait <= 0:
            self._flush()
        elif self._timer is None:
            try:
                self._timer = asyncio.get_running_loop().call_later(wait, self._on_timer)
            except RuntimeError:
                pass                                        # no loop: the next frame publishes

    def _on_timer(self) -> None:
        """The pacing timer fired: publish what changed."""
        self._timer = None
        if self._dirty:
            self._flush()

    def _cancel_timer(self) -> None:
        """Drop a pending pacing timer."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    async def _stop_sub(self) -> None:
        """Stop and forget the subscriber; no callback of it runs afterwards."""
        sub, self._sub = self._sub, None
        if sub is not None:
            await sub.stop()
