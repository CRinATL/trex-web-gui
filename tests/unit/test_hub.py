import asyncio

from trex_web_gui.api.hub import STATE_TYPES, Hub


def test_fanout_and_overflow_drops_oldest() -> None:
    async def run() -> None:
        h = Hub(maxsize=2)
        a = h.subscribe()
        b = h.subscribe()
        for i in range(3):
            h.publish({"i": i})
        assert [(await a.get())["i"] for _ in range(2)] == [1, 2]
        assert b.qsize() == 2 and h.dropped == 2
        h.unsubscribe(a)
        h.publish({"i": 9})
        assert a.qsize() == 0
    asyncio.run(run())


def test_state_messages_survive_overflow() -> None:
    async def run() -> None:
        hub = Hub(maxsize=5)
        sub = hub.subscribe()
        hub.publish({"type": "session", "state": "connecting"})
        for i in range(20):
            hub.publish({"type": "event", "name": f"e{i}"})
        hub.publish({"type": "ports", "ports": [1]})
        hub.publish({"type": "session", "state": "connected"})
        got = [await sub.get() for _ in range(sub.qsize())]
        kinds = [(m["type"], m.get("state")) for m in got]
        assert ("session", "connected") in kinds and ("ports", None) in kinds
        assert ("session", "connecting") not in kinds          # coalesced: only the newest of a type
        assert hub.dropped == 20 - (5 - 2)                     # only events were dropped
        assert len(got) == 5
    asyncio.run(run())


def test_coalesced_state_keeps_its_queue_position() -> None:
    async def run() -> None:
        hub = Hub()
        sub = hub.subscribe()
        hub.publish({"type": "ports", "ports": [1]})
        for i in range(3):
            hub.publish({"type": "log", "message": f"l{i}"})
        hub.publish({"type": "ports", "ports": [2]})
        first = await sub.get()
        # newest state, not behind the logs
        assert first == {"type": "ports", "ports": [2]} and sub.qsize() == 3
    asyncio.run(run())


def test_get_waits_for_publish() -> None:
    async def run() -> None:
        hub = Hub()
        sub = hub.subscribe()
        t = asyncio.create_task(sub.get())
        await asyncio.sleep(0.01)
        assert not t.done()
        hub.publish({"type": "log", "message": "x"})
        assert (await asyncio.wait_for(t, 1))["message"] == "x"
    asyncio.run(run())


def test_stats_messages_coalesce_to_the_latest() -> None:
    async def run() -> None:
        hub = Hub(maxsize=3)
        sub = hub.subscribe()
        hub.publish({"type": "stats", "ts": 1.0})
        for i in range(10):
            hub.publish({"type": "log", "message": f"l{i}"})
        hub.publish({"type": "stats", "ts": 2.0})
        hub.publish({"type": "stats", "ts": 3.0})
        got = [await sub.get() for _ in range(sub.qsize())]
        stats = [m for m in got if m["type"] == "stats"]
        assert stats == [{"type": "stats", "ts": 3.0}]          # one entry, the newest, never dropped
    asyncio.run(run())


def test_pgid_and_util_coalesce() -> None:
    async def run() -> None:
        hub = Hub(maxsize=3)
        sub = hub.subscribe()
        hub.publish({"type": "pgid", "ts": 1.0})
        hub.publish({"type": "util", "ts": 1.0})
        for i in range(10):
            hub.publish({"type": "log", "message": f"l{i}"})
        for ts in (2.0, 3.0):
            hub.publish({"type": "pgid", "ts": ts})
            hub.publish({"type": "util", "ts": ts})
        got = [await sub.get() for _ in range(sub.qsize())]
        # one entry each, the newest
        assert [m for m in got if m["type"] == "pgid"] == [{"type": "pgid", "ts": 3.0}]
        assert [m for m in got if m["type"] == "util"] == [{"type": "util", "ts": 3.0}]
    asyncio.run(run())


def test_all_twelve_state_types_can_be_queued_past_maxsize() -> None:
    async def run() -> None:
        hub = Hub(maxsize=1)
        sub = hub.subscribe()
        kinds = ("session", "ports", "stats", "pgid", "util", "port_labels", "busy", "capture",
                 "test_setups", "settings", "monitor", "daemon")
        assert frozenset(kinds) == STATE_TYPES
        for kind in kinds:
            hub.publish({"type": kind})
        assert sub.qsize() == 12 and hub.dropped == 0
    asyncio.run(run())


def test_test_setups_and_settings_are_state_types() -> None:
    async def run() -> None:
        hub = Hub()
        sub = hub.subscribe()
        for n in (1, 2):
            hub.publish({"type": "test_setups", "setups": [n], "load_errors": []})
            hub.publish({"type": "settings", "n": n})
        got = [await sub.get() for _ in range(sub.qsize())]
        assert got == [{"type": "test_setups", "setups": [2], "load_errors": []},
                       {"type": "settings", "n": 2}]
    asyncio.run(run())


def test_ping_messages_are_not_coalesced() -> None:
    async def run() -> None:
        hub = Hub()
        sub = hub.subscribe()
        for seq in range(1, 4):
            hub.publish({"type": "ping", "port": 1, "seq": seq})
        hub.publish({"type": "busy", "ports": {1: "ping"}})
        hub.publish({"type": "busy", "ports": {}})
        got = [await sub.get() for _ in range(sub.qsize())]
        assert [m["seq"] for m in got if m["type"] == "ping"] == [1, 2, 3]
        assert [m["ports"] for m in got if m["type"] == "busy"] == [{}]     # busy is state: only the newest
    asyncio.run(run())


def test_capture_is_a_state_type() -> None:
    async def run() -> None:
        hub = Hub()
        sub = hub.subscribe()
        for n in range(3):
            hub.publish({"type": "capture", "n": n})
        got = [await sub.get() for _ in range(sub.qsize())]
        assert got == [{"type": "capture", "n": 2}]
    asyncio.run(run())


def test_capture_pkts_capped_per_subscriber() -> None:
    async def run() -> None:
        hub = Hub(maxsize=1000)
        slow = hub.subscribe()
        other = hub.subscribe()
        for n in range(30):
            hub.publish({"type": "capture_pkts", "n": n})
        got = [await slow.get() for _ in range(slow.qsize())]
        assert len(got) == 20 and slow.dropped == 10 and other.dropped == 10 and hub.dropped == 20
        assert [m["n"] for m in got] == list(range(10, 30))       # the newest are kept
    asyncio.run(run())


def test_capture_pkts_dropped_before_capture_state() -> None:
    async def run() -> None:
        hub = Hub(maxsize=2)
        sub = hub.subscribe()
        hub.publish({"type": "capture", "v": 1})
        hub.publish({"type": "capture_pkts", "n": 1})
        hub.publish({"type": "capture_pkts", "n": 2})      # full: the oldest droppable goes, never the state
        got = [await sub.get() for _ in range(sub.qsize())]
        assert {"type": "capture", "v": 1} in got
        assert [m["n"] for m in got if m["type"] == "capture_pkts"] == [2]
        assert sub.dropped == 1
    asyncio.run(run())
