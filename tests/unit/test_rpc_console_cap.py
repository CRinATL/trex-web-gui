"""The RPC console caps what it publishes: a body whose JSON text passes RPC_BODY_MAX is cut."""
import asyncio
import json
from typing import Any

from trex_web_gui import trexapi
from trex_web_gui.sessions import TrexSession
from trex_web_gui.sessions.trex_session import RPC_BODY_MAX


def _body_of_length(n: int) -> dict[str, str]:
    """A dict holding one string, sized so that its JSON text is exactly n characters."""
    overhead = len(json.dumps({"k": ""}))
    return {"k": "a" * (n - overhead)}


async def _published(body: Any) -> list[dict[str, Any]]:
    """Feed one response record to a session whose console is on; return the `rpc` messages it published."""
    msgs: list[dict[str, Any]] = []
    s = TrexSession(msgs.append, release_on_exit=lambda: False)
    s._loop = asyncio.get_running_loop()
    s.set_rpc_console(True)
    s._on_rpc(trexapi.RpcRecord("response", body, 1.0, "t"))
    await asyncio.sleep(0)
    return [m for m in msgs if m.get("type") == "rpc"]


def test_rpc_body_at_the_cap_is_sent_whole() -> None:
    """A body whose JSON text is exactly RPC_BODY_MAX long is published unchanged, with no `truncated` key."""
    body = _body_of_length(RPC_BODY_MAX)
    assert len(json.dumps(body, default=str)) == RPC_BODY_MAX
    [m] = asyncio.run(_published(body))
    assert m["body"] == body
    assert "truncated" not in m


def test_rpc_body_over_the_cap_is_cut() -> None:
    """One character more is published as the first RPC_BODY_MAX characters, with the full length."""
    body = _body_of_length(RPC_BODY_MAX + 1)
    text = json.dumps(body, default=str)
    [m] = asyncio.run(_published(body))
    assert m["body"] == text[:RPC_BODY_MAX]
    assert m["truncated"] == RPC_BODY_MAX + 1
