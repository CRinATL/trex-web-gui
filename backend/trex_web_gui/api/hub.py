"""Fan-out of published messages to WebSocket subscribers. Loop-thread only.

State messages (STATE_TYPES) are coalesced: a newer one replaces the queued older one of its type in
place, so it keeps that older one's position and is not pushed behind a log backlog. Overflow never
drops them. Only log/event/rpc/ping and capture_pkts/capture_save messages are dropped, oldest first. A
slow browser tab therefore always ends up with the current session and ports state (P1 parked item, moved
to P2).
At most 12 state entries are ever queued, so for maxsize >= 1 the queue can exceed maxsize by up to 12, and
only when maxsize < 13. `ping` messages are not state: none is coalesced, but like log lines they are
droppable when a subscriber's queue overflows. A BULK_TYPES kind (`capture_pkts`) is also capped on its
own: a subscriber keeps at most BULK_TYPES[kind] queued of it, dropping the oldest of that kind (counted in
`dropped`), so a stalled tab holds 20 batches however large maxsize is and the `capture` state still arrives.
Replacing in place can deliver a `session` message ahead of `log` entries queued before it, for a slow
client. That is cosmetic, and accepted."""
import asyncio
import collections

STATE_TYPES = frozenset({"session", "ports", "stats", "pgid", "util", "port_labels", "busy", "capture",
                         "test_setups", "settings", "monitor", "daemon"})
# Bulk messages are droppable and never coalesced; each subscriber keeps at most this many queued of a kind.
BULK_TYPES = {"capture_pkts": 20}


class Subscription:
    def __init__(self, maxsize: int) -> None:
        """An empty queue; `maxsize` is the length at which a put evicts the oldest droppable message."""
        self._q: collections.deque[dict[str, object]] = collections.deque()
        self._ready = asyncio.Event()
        self._maxsize = maxsize
        self.dropped = 0

    def put(self, msg: dict[str, object]) -> None:
        """Queues `msg`: a state message replaces the queued one of its type in place; a bulk kind first
        drops its own oldest past its cap; at `maxsize` the oldest droppable message goes (counted in
        `dropped`). Wakes a waiting `get`. Loop thread only."""
        kind = msg.get("type")
        if kind in STATE_TYPES:
            for i, m in enumerate(self._q):
                if m.get("type") == kind:
                    self._q[i] = msg               # in place: the queue does not grow, nothing to drop
                    self._ready.set()
                    return
        cap = BULK_TYPES.get(kind) if isinstance(kind, str) else None
        if cap is not None:                    # a bulk kind is capped on its own, whatever maxsize says
            queued = [i for i, m in enumerate(self._q) if m.get("type") == kind]
            if len(queued) >= cap:
                del self._q[queued[0]]
                self.dropped += 1
        if len(self._q) >= self._maxsize:      # a new state entry also evicts the oldest droppable one
            for i, m in enumerate(self._q):
                if m.get("type") not in STATE_TYPES:
                    del self._q[i]
                    self.dropped += 1
                    break
        self._q.append(msg)
        self._ready.set()

    async def get(self) -> dict[str, object]:
        """The oldest queued message, waiting while the queue is empty."""
        while not self._q:
            self._ready.clear()
            await self._ready.wait()
        return self._q.popleft()

    def qsize(self) -> int:
        """The number of queued messages."""
        return len(self._q)


class Hub:
    def __init__(self, maxsize: int = 1000) -> None:
        """A hub with no subscribers; `maxsize` is the queue bound of each subscription it makes."""
        self._subs: set[Subscription] = set()
        self._maxsize = maxsize
        self.dropped = 0

    def subscribe(self) -> Subscription:
        """A new Subscription, fed by every publish until it is unsubscribed."""
        s = Subscription(self._maxsize)
        self._subs.add(s)
        return s

    def unsubscribe(self, s: Subscription) -> None:
        """Stops feeding `s`; a subscription that is not registered is ignored."""
        self._subs.discard(s)

    def publish(self, msg: dict[str, object]) -> None:
        """Puts `msg` on every subscription's queue and adds the messages they dropped to `dropped`. Loop
        thread only."""
        for s in self._subs:
            before = s.dropped
            s.put(msg)
            self.dropped += s.dropped - before
