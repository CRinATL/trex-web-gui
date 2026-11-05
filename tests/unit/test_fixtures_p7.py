"""The P7 EMU fixtures (tests/fixtures/p7/, recorded by vm/record_p7.py) exist, parse and keep their shape."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
P7 = ROOT / "tests" / "fixtures" / "p7"
PLUGINS = ROOT / "backend" / "trex_web_gui" / "_vendor" / "trex" / "emu" / "emu_plugins"


def _json(name: str) -> dict:
    """The parsed fixtures/p7/<name>."""
    return json.loads((P7 / name).read_text())


def _plugin_names() -> set[str]:
    """Plugins of the vendored EMU client that name a counter RPC (read as text, not imported)."""
    names = set()
    for path in PLUGINS.glob("emu_plugin_*.py"):
        if re.search(r"(ns|client)_cnt_rpc_cmd\s*=\s*'", path.read_text()):
            names.add(path.stem[len("emu_plugin_") :])
    return names


def test_files_exist_and_parse() -> None:
    for name in ("negatives.json", "counters.json", "summary.json", "profile_wire.json"):
        assert isinstance(_json(name), dict), name
    rows = [json.loads(line) for line in (P7 / "rpc.jsonl").read_text().splitlines()]
    assert rows and all({"client", "req", "resp"} <= set(r) for r in rows)
    assert {r["req"]["method"] for r in rows} >= {
        "api_sync_v2",
        "ctx_iter",
        "ctx_get_info",
        "ctx_client_iter",
        "ctx_client_get_info",
        "ctx_cnt",
        "ctx_add",
        "ctx_client_add",
    }


def test_profile_wire_has_two_namespaces_of_three_clients() -> None:
    reqs = _json("profile_wire.json")["requests"]
    adds = [r["params"] for r in reqs if r["method"] == "ctx_client_add"]
    assert [a["tun"]["vport"] for a in adds] == [0, 1]
    assert all(len(a["clients"]) == 3 for a in adds)
    assert len(next(r for r in reqs if r["method"] == "ctx_add")["params"]["tunnels"]) == 2


def test_summary_keys() -> None:
    s = _json("summary.json")
    assert {"handshake", "version", "drift", "port_prerequisite", "timings"} <= set(s)
    assert s["version"]["mode"] == "emulation"
    assert set(s["drift"]) == {"ctx_stop_autotriggers", "ctx_resource_monitor_get", "ipfix_ns_cnt"}
    assert all(d["code"] == -32601 for d in s["drift"].values())
    assert s["port_prerequisite"].startswith("none on the AF_PACKET guest")


def test_negatives_are_error_replies() -> None:
    neg = _json("negatives.json")
    assert {"api_sync_v2_major2", "ping_without_api_h", "ping_bogus_api_h"} <= set(neg)
    for name, pair in neg.items():
        assert "error" in pair["resp"], name
        assert isinstance(pair["resp"]["error"]["code"], int), name
    assert neg["ping_without_api_h"]["resp"]["error"]["code"] == -32602
    assert neg["api_sync_v2_major2"]["resp"]["error"]["code"] == -32600


def test_counters_have_ctx_meta_and_every_plugin() -> None:
    c = _json("counters.json")
    assert c["ctx"]["meta"] and c["ctx"]["values"]
    plugins = _plugin_names()
    assert len(plugins) >= 15
    assert set(c["plugins"]) == plugins
    for name, entry in c["plugins"].items():
        for scope in ("ns", "client"):
            if scope in entry:
                assert sum(k in entry[scope] for k in ("meta", "unsupported", "error")) == 1, (name, scope)
    metas = sum("meta" in e[scope] for e in c["plugins"].values() for scope in ("ns", "client") if scope in e)
    assert metas >= 15
    assert "unsupported" in c["plugins"]["ipfix"]["ns"]


def test_interrupted_iteration_recorded() -> None:
    s = _json("summary.json")
    walk = s["interrupted_iteration"]
    assert walk["first"]["stopped"] is False
    assert "result" in walk["after_reset_by_other"]  # a second client's reset does not interrupt
    for key in ("after_add_by_other", "after_remove_by_other"):
        assert walk[key]["error"]["code"] == -32600 and "interupted" in walk[key]["error"]["text"], key
    clients = s["interrupted_client_iteration"]
    assert "interupted" in clients["after_add_by_other"]["error"]["text"]
    assert "interupted" in json.dumps(_json("negatives.json")["ctx_iter_interrupted"]["resp"])


def _fixture_documents() -> list[tuple[str, object]]:
    """(file, parsed JSON) of every p7 fixture; rpc.jsonl contributes one document per line."""
    docs: list[tuple[str, object]] = []
    for path in sorted(P7.iterdir()):
        lines = path.read_text().splitlines() if path.suffix == ".jsonl" else [path.read_text()]
        docs += [(path.name, json.loads(line)) for line in lines]
    return docs


def _int_lists(value: object) -> list[list[int]]:
    """Every list of byte-sized ints (0..255) of length 4, 6 or 16 in a parsed JSON value, nested ones too.

    EMU sends addresses as such lists: a MAC is 6 of them, an IPv4 address 4, an IPv6 address 16."""
    found: list[list[int]] = []
    if isinstance(value, dict):
        for item in value.values():
            found += _int_lists(item)
    elif isinstance(value, list):
        ints = all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 255 for x in value)
        if value and ints and len(value) in (4, 6, 16):
            found.append(value)
        for item in value:
            found += _int_lists(item)
    return found


def _address_leaks(docs: list[tuple[str, object]]) -> list[str]:
    """MACs, IPv4 and IPv6 int lists in `docs` that are not the guest's or the fixture profile's."""
    leaks = []
    for name, doc in docs:
        for ints in _int_lists(doc):
            zero = not any(ints)
            if len(ints) == 6:
                ok = zero or ints[:4] == [0, 0, 0, 0x70]
            elif len(ints) == 4:
                ok = zero or ints[:2] == [1, 1] or ints == [127, 0, 0, 1]
            else:
                ok = (
                    zero or ints[:4] == [0x20, 0x01, 0x0D, 0xB8] or ints[:8] == [0xFE, 0x80, 0, 0, 0, 0, 0, 0]
                )
            if not ok:
                leaks.append(f"{name}: {ints}")
    return leaks


def test_no_employer_host_details() -> None:
    text = "\n".join(
        (P7 / n).read_text()
        for n in ("rpc.jsonl", "negatives.json", "counters.json", "summary.json", "profile_wire.json")
    )
    macs = set(re.findall(r"\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b", text, re.I))
    assert macs <= {"52:52:18:89:91:ea"}, macs
    for ip in set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)):
        assert ip.startswith(("1.1.", "127.0.0.", "0.0.0.")) or ip in {"05.05.2019"}, ip
    assert _address_leaks(_fixture_documents()) == []
