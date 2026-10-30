"""MonitorSession fed Frames directly, with an injected clock (no socket)."""
import asyncio
import json
import time

from trex_web_gui.monitor.session import (
    KNOWN_MAX_BYTES,
    OTHER_DATA_BYTES,
    OTHER_MAX,
    PUBLISH_EVERY_S,
    MonitorSession,
)
from trex_web_gui.monitor.wire import Frame


class Clock:
    def __init__(self) -> None:
        """Starts at 100 s."""
        self.t = 100.0

    def __call__(self) -> float:
        """The current fake time."""
        return self.t


def make() -> tuple[MonitorSession, list[dict], Clock]:
    """A session, the messages it publishes, and its clock."""
    out: list[dict] = []
    clock = Clock()
    return MonitorSession(out.append, clock=clock), out, clock


def fr(name: str, data: object) -> Frame:
    """A frame of `name` with `data`."""
    return Frame(name, data, len(json.dumps(data)))


def test_session_keeps_the_latest_known() -> None:
    s, _, _ = make()
    s.on_frame(fr("trex-global", {"m_tx_bps": 1}))
    s.on_frame(fr("trex-global", {"m_tx_bps": 2}))
    s.on_frame(fr("template_info", ["a", "b"]))
    snap = s.snapshot()
    assert snap["global"] == {"m_tx_bps": 2}
    assert snap["templates"] == ["a", "b"]
    assert snap["latency"] is None and snap["rx_check"] is None and snap["latency_v2"] is None
    assert set(snap["known_ts"]) == {"trex-global", "template_info"}  # type: ignore[arg-type]
    assert snap["frames"] == 3 and snap["other"] == {}


def test_session_caps_other_entries() -> None:
    s, _, _ = make()
    s.on_frame(fr("big", "x" * (2 << 20)))
    e = s.snapshot()["other"]["big"]  # type: ignore[index]
    assert e["truncated"] is True and len(e["data_text"]) == OTHER_DATA_BYTES == 65536 and e["bytes"] > 2e6
    assert "data" not in e
    s.on_frame(fr("small", {"a": 1}))
    assert s.snapshot()["other"]["small"]["data"] == {"a": 1}  # type: ignore[index]
    s2, _, _ = make()
    for i in range(25):
        s2.on_frame(fr(f"n{i}", i))
    snap = s2.snapshot()
    assert len(snap["other"]) == OTHER_MAX == 20  # type: ignore[arg-type]
    assert snap["dropped"]["other_names"] == 5  # type: ignore[index]
    s2.on_frame(fr("n0", 99))                       # a name already held still updates
    assert s2.snapshot()["other"]["n0"]["data"] == 99  # type: ignore[index]
    assert s2.snapshot()["dropped"]["other_names"] == 5  # type: ignore[index]


def test_known_names_never_starved() -> None:
    s, _, _ = make()
    for i in range(64):
        s.on_frame(fr(f"junk{i}", i))
    s.on_frame(fr("trex-global", {"m_tx_pps": 7}))
    snap = s.snapshot()
    assert snap["global"] == {"m_tx_pps": 7}
    assert len(snap["other"]) == OTHER_MAX  # type: ignore[arg-type]


def test_oversized_known_entry_keeps_the_previous_value() -> None:
    s, _, _ = make()
    s.on_frame(fr("trex-global", {"v": 1}))
    s.on_frame(fr("trex-global", {"v": "x" * KNOWN_MAX_BYTES}))
    snap = s.snapshot()
    assert snap["global"] == {"v": 1}
    assert snap["dropped"]["oversize"] == 1  # type: ignore[index]


def test_publish_rate_is_bounded() -> None:
    """Real time on a loop, so the trailing-timer publishes count too: 3 s of frames every 50 ms."""
    async def run() -> None:
        out: list[dict] = []
        s = MonitorSession(out.append)
        s.on_state("connected")
        end = time.monotonic() + 3.0
        i = 0
        while time.monotonic() < end:
            s.on_frame(fr("trex-global", {"i": i}))
            i += 1
            await asyncio.sleep(0.05)
        await asyncio.sleep(PUBLISH_EVERY_S + 0.1)          # the timer publishes the last frames
        s.on_state("stale")
        await s.close()
        assert len(out) <= 2 * 3 + 2 + 1
        assert len(out) >= 5                                # the timer did publish, not only the first frame
        assert out[0]["state"] == "connected" and out[-1]["state"] == "stale"
        assert out[-2]["global"] == {"i": i - 1}            # nothing left unpublished
    asyncio.run(run())


def test_long_names_are_refused() -> None:
    s, _, _ = make()
    s.on_frame(fr("n" * 257, 1))
    s.on_frame(fr("\u00e9" * 129, 1))                        # 258 bytes in UTF-8
    s.on_frame(fr("n" * 256, 2))
    snap = s.snapshot()
    assert list(snap["other"]) == ["n" * 256]  # type: ignore[call-overload]
    assert snap["dropped"]["long_name"] == 2  # type: ignore[index]
    assert snap["frames"] == 1
