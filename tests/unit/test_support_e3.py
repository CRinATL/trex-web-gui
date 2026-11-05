"""e3_mismatches (tests/support.py) on hand-made step lists: negative controls, with no process."""
from typing import Any

import pytest
from support import e3_mismatches
from trex_web_gui.trexapi.errors import ServerUnreachable
from trex_web_gui.trexapi.rawrpc import RawRpcError


def step(name: str, outcome: dict[str, Any], method: str = "ctx_add") -> dict[str, Any]:
    """One recorded step with empty params."""
    return {"name": name, "method": method, "params": {}, "outcome": outcome}


def test_e3_mismatches_reports_a_different_text() -> None:
    """An error of the same code and another text is one mismatch naming the step and both outcomes."""
    def call(_m: str, _p: dict[str, Any]) -> Any:
        """Answer with the wrong text."""
        raise RawRpcError(-32600, "other")

    steps = [step("a", {"error": {"code": -32600, "text": "wanted"}})]
    mismatches, skipped = e3_mismatches(call, steps)
    assert skipped == 0 and len(mismatches) == 1
    assert mismatches[0].startswith("a: expected ") and "wanted" in mismatches[0] and "other" in mismatches[0]


def test_e3_mismatches_accepts_an_equal_error_and_result() -> None:
    """An error equal on code and text and a result equal to the recorded one are no mismatch."""
    def call(m: str, _p: dict[str, Any]) -> Any:
        """Raise for ctx_add, answer True for the rest."""
        if m == "ctx_add":
            raise RawRpcError(-32600, "t")
        return True

    steps = [step("a", {"error": {"code": -32600, "text": "t"}}), step("b", {"result": True}, "ctx_remove")]
    assert e3_mismatches(call, steps) == ([], 0)


def test_e3_mismatches_stops_at_the_first_unanswered_step() -> None:
    """[answered A, unanswered B, answered C]: B and C are never sent, and 2 steps are not compared."""
    def call(m: str, _p: dict[str, Any]) -> Any:
        """Answer A; fail the test if B or C is sent."""
        if m != "a_method":
            raise AssertionError(f"sent {m}")
        return True

    steps = [step("A", {"result": True}, "a_method"),
             step("B", {"unanswered": "no reply", "guest": ""}, "b_method"),
             step("C", {"result": True}, "c_method")]
    assert e3_mismatches(call, steps) == ([], 2)


def test_e3_mismatches_compares_listings_as_sets() -> None:
    """A listing in another order matches; a different item set, or a different `stopped`, does not."""
    rec = {"result": {"empty": False, "stopped": False, "data": [{"v": 1}, {"v": 2}]}}
    steps = [step("l", rec, "ctx_iter")]

    def reordered(_m: str, _p: dict[str, Any]) -> Any:
        """The same items, reversed."""
        return {"empty": False, "stopped": False, "data": [{"v": 2}, {"v": 1}]}

    def fewer(_m: str, _p: dict[str, Any]) -> Any:
        """One item missing."""
        return {"empty": False, "stopped": False, "data": [{"v": 1}]}

    def stopped(_m: str, _p: dict[str, Any]) -> Any:
        """The same items with `stopped` set."""
        return {"empty": False, "stopped": True, "data": [{"v": 1}, {"v": 2}]}

    assert e3_mismatches(reordered, steps) == ([], 0)
    assert len(e3_mismatches(fewer, steps)[0]) == 1
    assert len(e3_mismatches(stopped, steps)[0]) == 1


def test_e3_mismatches_names_the_step_that_got_no_answer() -> None:
    """ServerUnreachable propagates, prefixed with the step's name."""
    def call(_m: str, _p: dict[str, Any]) -> Any:
        """No answer."""
        raise ServerUnreachable("gone")

    with pytest.raises(ServerUnreachable, match=r"^b: gone"):
        e3_mismatches(call, [step("b", {"result": True})])
