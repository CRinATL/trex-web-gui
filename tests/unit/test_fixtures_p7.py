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


GUEST_MAC = "52:52:18:89:91:ea"
FIXTURE_MAC_PREFIX = [0, 0, 0, 0x70]  # the MACs this suite's own scratch clients use (00:00:00:70:xx:xx)


def _mac_admitted(ints: list[int]) -> bool:
    """Whether a 6-byte MAC is all zeros or has the fixture prefix; the int and colon checks share it."""
    return not any(ints) or ints[:4] == FIXTURE_MAC_PREFIX


def _colon_mac_leaks(text: str) -> set[str]:
    """Colon-form MACs in `text` other than the guest's and the fixture prefix's.

    The server's own error texts render a client's MAC in colon form, so the scratch MACs the recorder adds
    appear that way; any other colon MAC is still a leak."""
    macs = set(re.findall(r"\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b", text, re.I))
    return {
        m for m in macs if m.lower() != GUEST_MAC and not _mac_admitted([int(b, 16) for b in m.split(":")])
    }


def _link_local_of_admitted_mac(ints: list[int]) -> bool:
    """True for an fe80::/64 address whose interface id is the EUI-64 of an admitted MAC (u/l bit flipped,
    ff:fe in the middle); any other interface id could carry a real host's MAC."""
    if len(ints) != 16 or ints[:8] != [0xFE, 0x80, 0, 0, 0, 0, 0, 0] or ints[11:13] != [0xFF, 0xFE]:
        return False
    return _mac_admitted([ints[8] ^ 0x02, ints[9], ints[10], ints[13], ints[14], ints[15]])


def _address_leaks(docs: list[tuple[str, object]]) -> list[str]:
    """MACs, IPv4 and IPv6 int lists in `docs` that are not the guest's or the fixture profile's."""
    leaks = []
    for name, doc in docs:
        for ints in _int_lists(doc):
            zero = not any(ints)
            if len(ints) == 6:
                ok = _mac_admitted(ints)
            elif len(ints) == 4:
                ok = zero or ints[:2] == [1, 1] or ints == [127, 0, 0, 1]
            else:
                ok = zero or ints[:4] == [0x20, 0x01, 0x0D, 0xB8] or _link_local_of_admitted_mac(ints)
            if not ok:
                leaks.append(f"{name}: {ints}")
    return leaks


def test_colon_mac_outside_the_fixture_prefix_is_a_leak() -> None:
    """Negative control: only the guest's MAC and the fixture prefix pass the colon-form check."""
    assert (
        _colon_mac_leaks("client with the same MAC 00:00:00:70:63:01 already exist, guest 52:52:18:89:91:ea")
        == set()
    )
    assert _colon_mac_leaks("client with the same MAC 00:00:00:71:63:01 already exist") == {
        "00:00:00:71:63:01"
    }
    assert _colon_mac_leaks("aa:bb:cc:dd:ee:ff") == {"aa:bb:cc:dd:ee:ff"}


def test_no_employer_host_details() -> None:
    text = "\n".join(
        (P7 / n).read_text()
        for n in ("rpc.jsonl", "negatives.json", "counters.json", "summary.json", "profile_wire.json")
    )
    # The server's error texts print a client's MAC in colon form, so the fixture-prefix MACs (00:00:00:70:..)
    # are admitted beside the guest's, as they are in int form; any other colon MAC fails.
    assert _colon_mac_leaks(text) == set()
    for ip in set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)):
        assert ip.startswith(("1.1.", "127.0.0.", "0.0.0.")) or ip in {"05.05.2019"}, ip
    assert _address_leaks(_fixture_documents()) == []


E3_NAMES = [
    "e3_setup_ctx_add",
    "e3_setup_client_add",
    "e3_ctx_add_duplicate",
    "e3_ctx_add_duplicate_in_request",
    "e3_ctx_iter_after_duplicate_in_request",
    "e3_ctx_add_new_and_existing",
    "e3_ctx_iter_after_new_and_existing",
    "e3_ctx_client_add_duplicate",
    "e3_ctx_client_add_duplicate_in_request",
    "e3_ctx_client_iter_after_duplicate_in_request",
    "e3_ctx_client_add_unknown_tunnel",
    "e3_ctx_client_set_def_plugins_unknown_tunnel",
    "e3_ctx_client_remove_unknown_mac",
    "e3_ctx_client_remove_unknown_tunnel",
    "e3_ctx_client_iter_unknown_tunnel",
    "e3_ctx_get_info_unknown_tunnel",
    "e3_ctx_get_info_known_and_unknown",
    "e3_ctx_client_get_info_unknown_tunnel",
    "e3_ctx_client_get_info_unknown_mac",
    "e3_ns_counter_unknown_tunnel",
    "e3_client_counter_unknown_mac",
    "e3_client_counter_unknown_tunnel",
    "e3_client_counter_plugin_not_loaded",
    "e3_ctx_remove_unknown_tunnel",
    "e3_setup_ctx_add_empty",
    "e3_ctx_remove_known_and_unknown",
    "e3_ctx_iter_after_remove_known_and_unknown",
    "e3_ctx_remove_with_clients",
    "e3_ctx_iter_after_remove_with_clients",
]


def test_e3_steps_are_recorded_in_order_with_their_condition() -> None:
    e3 = _json("summary.json")["e3"]
    assert [s["name"] for s in e3["steps"]] == E3_NAMES
    assert "standalone" in e3["condition"] and "v3.08" in e3["condition"]
    by_name = {s["name"]: s for s in e3["steps"]}
    for row in e3["resetup"]:
        assert "unanswered" in by_name[row["after"]]["outcome"], row["after"]
    for step in e3["steps"] + e3["resetup"]:
        assert "api_h" not in step["params"], step["name"]
        forms = [k for k in ("result", "error", "unanswered") if k in step["outcome"]]
        assert len(forms) == 1 and len(step["outcome"]) == (2 if forms == ["unanswered"] else 1), step["name"]
        if forms == ["unanswered"]:
            assert set(step["outcome"]) == {"unanswered", "guest"}, step["name"]


def test_e3_error_steps_are_in_negatives() -> None:
    neg = _json("negatives.json")
    errors = [s for s in _json("summary.json")["e3"]["steps"] if "error" in s["outcome"]]
    assert errors
    for step in errors:
        got = neg[step["name"]]["resp"]["error"]
        assert got["code"] == step["outcome"]["error"]["code"], step["name"]
        assert got["message"] == step["outcome"]["error"]["text"], step["name"]


def test_client_scope_no_valid_plugin_is_recorded() -> None:
    got = _json("negatives.json")["e3_client_counter_plugin_not_loaded"]["resp"]["error"]["message"]
    assert got == "Error there is no valid plugin dhcp for this client "


def test_unanswered_requests_are_logged() -> None:
    rows = [json.loads(line) for line in (P7 / "rpc.jsonl").read_text().splitlines()]
    assert any(r["req"]["method"] == "ctx_remove" and r["resp"] is None for r in rows)
    # only the ipfix scratch removal went unanswered (no E3 step did); a close never re-logs an answered row
    nulls = [r for r in rows if r["resp"] is None]
    assert [r["req"]["method"] for r in nulls] == ["ctx_remove"]
    answered = [
        (r["t"], r["client"], json.dumps(r["req"], sort_keys=True)) for r in rows if r["resp"] is not None
    ]
    assert len(answered) == len(set(answered))


def test_ipfix_panic_is_kept_whole() -> None:
    removal = _json("summary.json")["ipfix_scratch_removal"]
    assert "RemovePlugins" in removal["guest"]
    assert removal["condition"] == "ipfix at namespace level only, empty {} config, standalone trex-emu v3.08"


def test_a_link_local_address_is_admitted_only_for_an_admitted_mac() -> None:
    """Negative control: fe80::/64 passes only with the EUI-64 interface id of an admitted MAC; the same
    prefix carrying any other host's MAC, or no ff:fe, is a leak."""
    fe80 = [0xFE, 0x80, 0, 0, 0, 0, 0, 0]
    ours = [0x02, 0, 0, 0xFF, 0xFE, 0x70, 0x63, 0x01]          # 00:00:00:70:63:01, the fixture prefix
    theirs = [0x50, 0x54, 0x00, 0xFF, 0xFE, 0x12, 0x34, 0x56]  # 52:54:00:12:34:56, a made-up host MAC
    assert _address_leaks([("ok", [fe80 + ours])]) == []
    assert _address_leaks([("bad", [fe80 + theirs])]) == [f"bad: {fe80 + theirs}"]
    assert _address_leaks([("no-eui", [fe80 + [1, 2, 3, 4, 5, 6, 7, 8]])]) != []
