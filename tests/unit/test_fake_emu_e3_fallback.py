"""tests/fake/fake_emu.py when the E3 recording holds an unanswered step: that step and every later one decide
nothing (they ran against a restarted server), so each write rule the fake derives from E3 takes the
fallback its comment names. The recorded run had no unanswered step, so only these tests reach the fallbacks.
The fake is loaded from a copy beside a copy of tests/fixtures/p7 whose summary.json is edited."""
import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
FLAGS = ("ADD_KEEPS", "REMOVE_IN_ORDER", "REMOVE_REFUSES_CLIENTS", "CLIENT_ADD_KEEPS", "GET_INFO_FAILS",
         "CLIENT_INFO_REFUSES")
# each flag's value when the E3 steps it reads decide nothing
FALLBACK = {"ADD_KEEPS": True, "REMOVE_IN_ORDER": False, "REMOVE_REFUSES_CLIENTS": False,
            "CLIENT_ADD_KEEPS": False, "GET_INFO_FAILS": True, "CLIENT_INFO_REFUSES": False}
# the E3 steps each flag reads (the latest one decides whether a crash reaches it)
READS = {"ADD_KEEPS": ["e3_ctx_iter_after_duplicate_in_request", "e3_ctx_iter_after_new_and_existing"],
         "REMOVE_IN_ORDER": ["e3_ctx_iter_after_remove_known_and_unknown"],
         "REMOVE_REFUSES_CLIENTS": ["e3_ctx_remove_with_clients"],
         "CLIENT_ADD_KEEPS": ["e3_ctx_client_iter_after_duplicate_in_request"],
         "GET_INFO_FAILS": ["e3_ctx_get_info_known_and_unknown"],
         "CLIENT_INFO_REFUSES": ["e3_ctx_client_get_info_unknown_mac"]}


UNANSWERED = {"unanswered": "no reply within 10.0 s", "guest": "panic: test"}  # as record_p7.py writes one


def load_fake(tmp_path: Path, unanswered: str | None, outcomes: dict[str, dict] | None = None) -> ModuleType:
    """fake_emu.py loaded from a copy under tmp_path whose summary.json marks E3 step `unanswered` (None: as
    recorded) with the outcome record_p7.py writes for a step the server never answered, and gives each step
    named in `outcomes` that outcome instead of the recorded one."""
    fake_dir, fix = tmp_path / "tests" / "fake", tmp_path / "tests" / "fixtures" / "p7"
    fake_dir.mkdir(parents=True)
    shutil.copy(ROOT / "tests" / "fake" / "fake_emu.py", fake_dir / "fake_emu.py")
    shutil.copytree(ROOT / "tests" / "fixtures" / "p7", fix)
    edits = {**(outcomes or {}), **({unanswered: UNANSWERED} if unanswered else {})}
    if edits:
        summary = json.loads((fix / "summary.json").read_text())
        for step in summary["e3"]["steps"]:
            step["outcome"] = edits.pop(step["name"], step["outcome"])
        assert not edits, f"no such E3 step: {sorted(edits)}"
        (fix / "summary.json").write_text(json.dumps(summary))
    spec = importlib.util.spec_from_file_location(f"fake_emu_{tmp_path.name}", fake_dir / "fake_emu.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    """The fake over the fixtures as recorded."""
    return load_fake(tmp_path_factory.mktemp("recorded"), None)


def test_the_recording_decides_every_rule(recorded: ModuleType) -> None:
    """As recorded no step went unanswered, so each flag is the recorded behaviour; at least four differ from
    their fallback, which is what lets the crash tests below see a fallback at all."""
    assert not any("unanswered" in s["outcome"] for s in recorded.SUM["e3"]["steps"])
    assert all(recorded.decided(s) is not None for steps in READS.values() for s in steps)
    assert sum(getattr(recorded, f) != FALLBACK[f] for f in FLAGS) >= 4


def test_a_crash_on_the_first_step_leaves_every_rule_at_its_fallback(tmp_path: Path) -> None:
    """The first setup step unanswered: no E3 step decides anything, so every flag is its fallback."""
    fake = load_fake(tmp_path, "e3_setup_ctx_add")
    assert {f: getattr(fake, f) for f in FLAGS} == FALLBACK
    assert fake.decided("e3_ctx_iter_after_remove_with_clients") is None
    assert fake.listing("e3_ctx_iter_after_duplicate_in_request") is None


@pytest.mark.parametrize("crash", ["e3_ctx_client_add_unknown_tunnel", "e3_ctx_remove_unknown_tunnel",
                                   "e3_ctx_remove_with_clients"])
def test_a_crash_decides_only_the_steps_before_it(tmp_path: Path, recorded: ModuleType, crash: str) -> None:
    """A step unanswered mid-run: a flag whose steps all ran before it keeps the recorded value, and one that
    reads the crashed step or any later one falls back, even though those later steps hold outcomes."""
    fake = load_fake(tmp_path, crash)
    order = [s["name"] for s in fake.SUM["e3"]["steps"]]
    for flag, steps in READS.items():
        before = max(order.index(s) for s in steps) < order.index(crash)
        want = getattr(recorded, flag) if before else FALLBACK[flag]
        assert getattr(fake, flag) == want, (flag, crash)
    assert fake.decided(crash) is None and fake.decided(order[order.index(crash) - 1]) is not None


def _listed(recorded: ModuleType, step: str, drop: object) -> dict:
    """Step `step`'s recorded listing result without the item `drop` (a vport's namespace, or a MAC)."""
    out = recorded.decided(step)
    data = out["result"]["data"]
    kept = [i for i in data if i != drop and not (isinstance(i, dict) and i.get("vport") == drop)]
    assert kept != data, (step, drop)
    return {"result": {**out["result"], "data": kept}}


def test_a_different_recorded_answer_decides_each_rule_the_other_way(tmp_path: Path,
                                                                     recorded: ModuleType) -> None:
    """Each rule follows what the server answered, not a constant: a re-record whose E3 steps answered the
    other way (a result where an error was recorded, a listing without the item) turns every flag over."""
    r = recorded
    other = {
        "e3_ctx_iter_after_duplicate_in_request": _listed(r, "e3_ctx_iter_after_duplicate_in_request",
                                                          r.SCRATCH_ADDED),
        "e3_ctx_iter_after_remove_known_and_unknown": {"result": {"data": [{"vport": r.SCRATCH_REMOVED}]}},
        "e3_ctx_remove_with_clients": {"result": {}},
        "e3_ctx_client_iter_after_duplicate_in_request": _listed(
            r, "e3_ctx_client_iter_after_duplicate_in_request", r.REPEATED_MAC),
        "e3_ctx_get_info_known_and_unknown": {"result": []},
        "e3_ctx_client_get_info_unknown_mac": {"result": []},
    }
    fake = load_fake(tmp_path, None, other)
    assert {f: getattr(fake, f) for f in FLAGS} == {f: not getattr(r, f) for f in FLAGS}
