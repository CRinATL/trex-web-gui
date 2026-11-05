"""validate_emu_profile (P7b Task 10): the worker's output is untrusted and is checked before any RPC."""
import copy
import json

import pytest
from support import FIXTURES
from trex_web_gui.trexapi import ProfileError, emu_profile
from trex_web_gui.trexapi.emu import MAX_NAMESPACES
from trex_web_gui.trexapi.emu_profile import validate_emu_profile


def good():
    """The recorded profile's data rebuilt as the worker returns it (a fresh object each call)."""
    reqs = json.loads((FIXTURES / "p7" / "profile_wire.json").read_text())["requests"]
    tunnels = reqs[1]["params"]["tunnels"]
    spaces = [{"key": {k: t[k] for k in ("vport", "tci", "tpid")}, "fields": t,
               "def_c_plugs": t["def_c_plugs"],
               "clients": reqs[3 + 2 * i]["params"]["clients"]} for i, t in enumerate(tunnels)]
    return {"def_ns_plugs": reqs[0]["params"]["def_plugs"], "namespaces": spaces}


def test_the_recorded_profile_is_valid():
    facts = validate_emu_profile(good())
    assert (facts.namespaces, facts.clients, facts.plugins, facts.vports) == (2, 6, ["arp", "icmp"], [0, 1])


def _bad(mutate, path):
    """A good profile changed by `mutate`, which must be refused with `path` in the message."""
    obj = good()
    mutate(obj)
    with pytest.raises(ProfileError, match=path):
        validate_emu_profile(obj)


def _set_key(obj, **kw):
    """Change the first namespace's key and its fields alike, so only the value under test is wrong."""
    obj["namespaces"][0]["key"].update(kw)
    obj["namespaces"][0]["fields"].update(kw)


@pytest.mark.parametrize("value", [256, -1, True, "0", 1.5, None])
def test_vport_is_an_int_0_to_255(value):
    _bad(lambda o: _set_key(o, vport=value), r"namespaces\[0\]\.key\.vport")


@pytest.mark.parametrize("field", ["tci", "tpid"])
@pytest.mark.parametrize("value", [[0, 0, 0], [65536, 0], [-1], ["1"], 5, [True], None])
def test_tags_are_at_most_two_ints_0_to_65535(field, value):
    _bad(lambda o: _set_key(o, **{field: value}), rf"namespaces\[0\]\.key\.{field}")


@pytest.mark.parametrize("mac", [[0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 256], [0, 0, 0, 0, 0, -1],
                                 "00:00:00:70:00:01", [0, 0, 0, 0, 0, True], None])
def test_mac_is_six_ints_0_to_255(mac):
    _bad(lambda o: o["namespaces"][0]["clients"][1].update(mac=mac), r"clients\[1\]\.mac")


def test_a_client_without_a_mac_is_refused():
    _bad(lambda o: o["namespaces"][0]["clients"][0].pop("mac"), r"clients\[0\]\.mac")


def test_macs_are_unique_per_namespace_but_may_repeat_across_namespaces():
    def twice(o):
        c = o["namespaces"][0]["clients"]
        c[2]["mac"] = list(c[0]["mac"])
    _bad(twice, r"clients\[2\]\.mac.*twice")
    obj = good()
    obj["namespaces"][1]["clients"][0]["mac"] = list(obj["namespaces"][0]["clients"][0]["mac"])
    validate_emu_profile(obj)


def test_namespace_keys_are_unique():
    def twice(o):
        o["namespaces"][1]["key"] = copy.deepcopy(o["namespaces"][0]["key"])
        o["namespaces"][1]["fields"].update(o["namespaces"][1]["key"])
    _bad(twice, r"namespaces\[1\]\.key.*twice")


@pytest.mark.parametrize("field,value",
                         [("vport", 1.0), ("vport", True), ("tci", [0.0, 0]), ("tci", [False, 0])])
def test_fields_match_the_key_type_strictly(field, value):
    """1.0 and True equal 1 under ==, and fields is what ctx_add sends."""
    obj = good()
    obj["namespaces"][1]["fields"][field] = value
    with pytest.raises(ProfileError, match=rf"namespaces\[1\]\.fields\.{field}"):
        validate_emu_profile(obj)


def test_fields_must_match_the_key():
    _bad(lambda o: o["namespaces"][0]["fields"].update(vport=9), r"namespaces\[0\]\.fields.*key")


PLUG_PATHS = {"def_ns": r"profile\.def_ns_plugs", "fields_plugs": r"namespaces\[0\]\.fields\.plugs",
              "fields_def": r"namespaces\[0\]\.fields\.def_c_plugs",
              "ns_def": r"namespaces\[0\]\.def_c_plugs",
              "client_plugs": r"namespaces\[0\]\.clients\[0\]\.plugs"}


@pytest.mark.parametrize("where", list(PLUG_PATHS))
@pytest.mark.parametrize("value", [[], {"arp": 1}, {"arp": []}, "x"])
def test_plugin_tables_are_dicts_of_dicts(where, value):
    def put(o):
        ns = o["namespaces"][0]
        if where == "def_ns":
            o["def_ns_plugs"] = value
        elif where == "fields_plugs":
            ns["fields"]["plugs"] = value
        elif where == "fields_def":
            ns["fields"]["def_c_plugs"] = value
        elif where == "ns_def":
            ns["def_c_plugs"] = value
        else:
            ns["clients"][0]["plugs"] = value
    _bad(put, PLUG_PATHS[where])


@pytest.mark.parametrize("obj", [[], None, {"namespaces": []}, {"def_ns_plugs": {}, "namespaces": {}}])
def test_top_level_shape(obj):
    with pytest.raises(ProfileError, match="profile"):
        validate_emu_profile(obj)


def test_namespace_and_clients_shapes():
    _bad(lambda o: o["namespaces"].__setitem__(0, []), r"namespaces\[0\]")
    _bad(lambda o: o["namespaces"][0].update(clients={}), r"namespaces\[0\]\.clients")
    _bad(lambda o: o["namespaces"][0]["clients"].__setitem__(0, 5), r"clients\[0\]")


def test_namespace_count_is_bounded():
    obj = {"def_ns_plugs": {}, "namespaces": [None] * (MAX_NAMESPACES + 1)}
    with pytest.raises(ProfileError, match=rf"{MAX_NAMESPACES + 1} namespaces; at most {MAX_NAMESPACES}"):
        validate_emu_profile(obj)


def test_client_count_is_bounded(monkeypatch):
    monkeypatch.setattr(emu_profile, "MAX_PROFILE_CLIENTS", 5)
    _bad(lambda o: None, r"namespaces\[1\]\.clients.*more than 5 clients")


def test_client_cap_is_the_constraint_value():
    assert emu_profile.MAX_PROFILE_CLIENTS == 262144


def test_serialised_size_is_bounded(monkeypatch):
    monkeypatch.setattr(emu_profile, "MAX_RESULT", 100)
    _bad(lambda o: None, "serialised size")


def test_namespace_client_count_is_bounded(monkeypatch):
    """A namespace over MAX_CLIENT_KEYS clients is refused with its path, so every namespace that loads can be
    removed by one bounded client walk (ruling R27)."""
    monkeypatch.setattr(emu_profile, "MAX_CLIENT_KEYS", 2)
    _bad(lambda o: None, r"namespaces\[0\]\.clients.*3 clients in one namespace; at most 2")


def test_namespace_client_cap_is_the_listing_cap():
    assert emu_profile.MAX_CLIENT_KEYS == 65536


@pytest.mark.parametrize("plugin", ["ipfix", "tdl"])
def test_namespace_level_ipfix_and_tdl_are_refused(plugin):
    """Removing a namespace with ipfix or tdl at namespace level crashes trex-emu v3.08 (measured, ruling
    R33), so such a profile never loads; the path and the plugin are named."""
    _bad(lambda o: o["def_ns_plugs"].update({plugin: {}}), rf"profile\.def_ns_plugs\.{plugin}.*crashes")
    _bad(lambda o: o["namespaces"][1]["fields"]["plugs"].update({plugin: {}}),
         rf"namespaces\[1\]\.fields\.plugs\.{plugin}.*crashes")


def test_client_level_ipfix_is_allowed():
    """The stock simple_ipfix.py puts ipfix on its clients; that loads and removes cleanly (E1)."""
    obj = good()
    obj["namespaces"][0]["def_c_plugs"]["ipfix"] = {}
    obj["namespaces"][0]["fields"]["def_c_plugs"]["ipfix"] = {}
    obj["namespaces"][0]["clients"][0]["plugs"] = {"ipfix": {}, "tdl": {}}
    assert "ipfix" in validate_emu_profile(obj).plugins


def test_mdns_clients_need_mdns_in_their_namespace():
    """A client with mdns in a namespace without mdns crashes trex-emu v3.08 (R33): refused in a default
    client table or a client's plugins, named by path."""
    _bad(lambda o: o["namespaces"][0].update(def_c_plugs={"mdns": {}}),
         r"namespaces\[0\]\.def_c_plugs\.mdns.*crashes")
    _bad(lambda o: o["namespaces"][0]["fields"]["def_c_plugs"].update(mdns={}),
         r"namespaces\[0\]\.fields\.def_c_plugs\.mdns")
    _bad(lambda o: o["namespaces"][1]["clients"][2].update(plugs={"mdns": {}}),
         r"namespaces\[1\]\.clients\[2\]\.plugs\.mdns")


def test_mdns_at_both_levels_is_allowed():
    """mdns in the namespace's own plugins (its plugs, or def_ns_plugs) allows it in the clients."""
    obj = good()
    obj["namespaces"][0]["fields"]["plugs"]["mdns"] = {}
    obj["namespaces"][0]["def_c_plugs"]["mdns"] = {}
    obj["namespaces"][0]["clients"][0]["plugs"] = {"mdns": {}}
    validate_emu_profile(obj)
    other = good()
    other["def_ns_plugs"]["mdns"] = {}
    other["namespaces"][1]["clients"][0]["plugs"] = {"mdns": {}}
    validate_emu_profile(other)
