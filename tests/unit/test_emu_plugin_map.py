"""PLUGIN_COUNTERS (trexapi/emu.py) against the vendored plugin classes and the recorded counters."""
import ast
import json
from pathlib import Path

from trex_web_gui.trexapi.emu import COUNTER_SCOPES, PLUGIN_COUNTERS

ROOT = Path(__file__).resolve().parent.parent.parent
PLUGINS = ROOT / "backend" / "trex_web_gui" / "_vendor" / "trex" / "emu" / "emu_plugins"
COUNTERS = json.loads((ROOT / "tests" / "fixtures" / "p7" / "counters.json").read_text())["plugins"]


def declared() -> dict[str, tuple[str | None, str | None]]:
    """plugin -> (ns counter method, client counter method) from each vendored plugin's `__init__` super call,
    read with ast (importing trex.emu is the worker's business, not the backend's). The key is the module
    name without `emu_plugin_`."""
    out: dict[str, tuple[str | None, str | None]] = {}
    for f in sorted(PLUGINS.glob("emu_plugin_*.py")):
        name = f.stem.removeprefix("emu_plugin_")
        if name == "base":
            continue
        found: dict[str, str] = {}
        for node in ast.walk(ast.parse(f.read_text())):
            is_init = isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "__init__"
            if is_init:
                for kw in node.keywords:
                    is_cmd = kw.arg in ("ns_cnt_rpc_cmd", "client_cnt_rpc_cmd")
                    if is_cmd and isinstance(kw.value, ast.Constant):
                        found[kw.arg] = kw.value.value
        out[name] = (found.get("ns_cnt_rpc_cmd"), found.get("client_cnt_rpc_cmd"))
    return out


def test_plugin_counters_match_vendored_plugins() -> None:
    """The map equals what the vendored plugin classes declare, so a pin bump that adds or renames a plugin
    counter fails here."""
    assert PLUGIN_COUNTERS == declared()
    assert len(PLUGIN_COUNTERS) == 17


def test_plugin_counters_match_recorded_counters() -> None:
    """Each plugin recorded in counters.json has exactly the scopes and commands the map names, and each
    scope recorded one outcome: meta and values, `unsupported`, or an `error`."""
    assert set(COUNTERS) == set(PLUGIN_COUNTERS)
    for name, (ns_cmd, client_cmd) in PLUGIN_COUNTERS.items():
        rec = {k: v for k, v in COUNTERS[name].items() if isinstance(v, dict)}
        assert {k: v["cmd"] for k, v in rec.items()} == {
            k: c for k, c in (("ns", ns_cmd), ("client", client_cmd)) if c}, name
        for scope, entry in rec.items():
            outcomes = [k for k in ("meta", "unsupported", "error") if k in entry]
            assert len(outcomes) == 1, (name, scope, outcomes)


def test_counter_scopes_name_every_command_once() -> None:
    """COUNTER_SCOPES has ctx_cnt and one entry per mapped command, in the scope the map puts it in."""
    cmds = [c for pair in PLUGIN_COUNTERS.values() for c in pair if c]
    assert len(cmds) == len(set(cmds))
    assert COUNTER_SCOPES == {"ctx_cnt": "ctx"} | {
        c: ("ns" if i == 0 else "client")
        for pair in PLUGIN_COUNTERS.values() for i, c in enumerate(pair) if c}
