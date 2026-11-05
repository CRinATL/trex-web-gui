"""Validation of an EMU profile's wire data, as the profile worker returned it (spec §4.3, §4.6).

The worker ran arbitrary profile code, so what it prints is untrusted data: the backend checks its shape
here, before any RPC is built from it. Deeper meaning (address formats, plugin options) is left to the EMU
server, which validates them itself. The first bad path is named in the ProfileError."""
import json
from dataclasses import dataclass
from typing import Any

from .emu import MAX_CLIENT_KEYS, MAX_NAMESPACES
from .profiles import MAX_RESULT, ProfileError

MAX_PROFILE_CLIENTS = 262144      # clients in all namespaces of one profile
TAG_MAX = 65535                   # a VLAN tci or tpid
MAC_BYTES = 6
# Profiles that crash trex-emu v3.08, measured by experiment E1 (ruling R33): refused before any RPC
NS_CRASHERS = {"ipfix", "tdl"}
NS_CRASH_WHY = "a namespace with this plugin is removed, which a later load or removal does"
MDNS_WHY = "a client with mdns is added to a namespace that does not have mdns itself"


@dataclass(frozen=True)
class ProfileFacts:
    namespaces: int
    clients: int
    plugins: list[str]            # sorted
    vports: list[int]             # distinct, sorted


def _int(v: Any, lo: int, hi: int) -> bool:
    """True for an int (never a bool) in lo..hi."""
    return type(v) is int and lo <= v <= hi


def _plugs(v: Any, path: str, names: set[str]) -> None:
    """Check that v is a dict of dicts (plugin name to options) and add the names to `names`."""
    if not isinstance(v, dict):
        raise ProfileError(f"{path}: a plugin table must be an object")
    for name, opts in v.items():
        if not isinstance(opts, dict):
            raise ProfileError(f"{path}.{name}: plugin options must be an object")
        names.add(name)


def _refuse_plugs(plugs: dict[str, Any], path: str, banned: set[str], why: str) -> None:
    """ProfileError naming `path` and the plugin when the (already validated) plugin table holds a plugin
    of `banned`; `why` completes the sentence."""
    for name in sorted(banned & set(plugs)):
        raise ProfileError(f"{path}.{name}: trex-emu v3.08 crashes when {why} (measured, ruling R33)")


def _tags(v: Any, path: str) -> tuple[int, ...]:
    """A tci or tpid: a list of at most two ints 0..65535, as a tuple."""
    if not (isinstance(v, list) and len(v) <= 2 and all(_int(x, 0, TAG_MAX) for x in v)):
        raise ProfileError(f"{path}: a list of at most 2 integers 0..{TAG_MAX}")
    return tuple(v)


def _key(v: Any, path: str) -> tuple[int, tuple[int, ...], tuple[int, ...]]:
    """A namespace key {vport, tci, tpid} as a tuple."""
    if not isinstance(v, dict) or set(v) != {"vport", "tci", "tpid"}:
        raise ProfileError(f"{path}: an object with vport, tci and tpid")
    if not _int(v["vport"], 0, 255):
        raise ProfileError(f"{path}.vport: an integer 0..255")
    return (v["vport"], _tags(v["tci"], f"{path}.tci"), _tags(v["tpid"], f"{path}.tpid"))


def validate_emu_profile(obj: Any) -> ProfileFacts:
    """Check the `profile` object of a worker's emu_load result; returns the counts, plugin names and vports
    computed here, never the worker's own. ProfileError naming the first bad path: a wrong type anywhere,
    a vport outside 0..255, a tci or tpid that is not at most two ints 0..65535, a MAC that is not six ints
    0..255, a MAC twice in one namespace, a namespace key twice, a plugin table that is not a dict of dicts,
    more than MAX_CLIENT_KEYS clients in one namespace, a namespace whose `fields` disagree with its
    key, more than MAX_NAMESPACES namespaces or MAX_PROFILE_CLIENTS clients, or a serialised size over
    MAX_RESULT bytes. Also the two profiles that crash trex-emu v3.08 (measured, ruling R33): ipfix or tdl
    at namespace level (def_ns_plugs, a namespace's plugs), and mdns in a default client table or a client's
    plugs when the namespace itself (its plugs with def_ns_plugs) lacks mdns."""
    if not isinstance(obj, dict) or set(obj) != {"def_ns_plugs", "namespaces"}:
        raise ProfileError("profile: an object with def_ns_plugs and namespaces")
    names: set[str] = set()
    _plugs(obj["def_ns_plugs"], "profile.def_ns_plugs", names)
    _refuse_plugs(obj["def_ns_plugs"], "profile.def_ns_plugs", NS_CRASHERS, NS_CRASH_WHY)
    spaces = obj["namespaces"]
    if not isinstance(spaces, list):
        raise ProfileError("profile.namespaces: a list")
    if len(spaces) > MAX_NAMESPACES:
        raise ProfileError(f"profile.namespaces: {len(spaces)} namespaces; at most {MAX_NAMESPACES}")
    seen: set[tuple[int, tuple[int, ...], tuple[int, ...]]] = set()
    clients = 0
    for i, ns in enumerate(spaces):
        at = f"profile.namespaces[{i}]"
        if not isinstance(ns, dict) or set(ns) != {"key", "fields", "def_c_plugs", "clients"}:
            raise ProfileError(f"{at}: an object with key, fields, def_c_plugs and clients")
        key = _key(ns["key"], f"{at}.key")
        if key in seen:
            raise ProfileError(f"{at}.key: namespace {ns['key']} appears twice")
        seen.add(key)
        fields = ns["fields"]
        if not isinstance(fields, dict) or set(fields) != {"vport", "tci", "tpid", "plugs", "def_c_plugs"}:
            raise ProfileError(f"{at}.fields: an object with vport, tci, tpid, plugs and def_c_plugs")
        # through _key, so 1.0 or True in fields never equals 1 in the key
        if _key({k: fields[k] for k in ("vport", "tci", "tpid")}, f"{at}.fields") != key:
            raise ProfileError(f"{at}.fields: vport, tci and tpid differ from the key")
        _plugs(fields["plugs"], f"{at}.fields.plugs", names)
        _plugs(fields["def_c_plugs"], f"{at}.fields.def_c_plugs", names)
        _plugs(ns["def_c_plugs"], f"{at}.def_c_plugs", names)
        _refuse_plugs(fields["plugs"], f"{at}.fields.plugs", NS_CRASHERS, NS_CRASH_WHY)
        has_mdns = "mdns" in fields["plugs"] or "mdns" in obj["def_ns_plugs"]
        if not has_mdns:
            _refuse_plugs(fields["def_c_plugs"], f"{at}.fields.def_c_plugs", {"mdns"}, MDNS_WHY)
            _refuse_plugs(ns["def_c_plugs"], f"{at}.def_c_plugs", {"mdns"}, MDNS_WHY)
        rows = ns["clients"]
        if not isinstance(rows, list):
            raise ProfileError(f"{at}.clients: a list")
        if len(rows) > MAX_CLIENT_KEYS:
            raise ProfileError(f"{at}.clients: {len(rows)} clients in one namespace; at most "
                               f"{MAX_CLIENT_KEYS} (removal walks a namespace's clients in one listing)")
        clients += len(rows)
        if clients > MAX_PROFILE_CLIENTS:
            raise ProfileError(f"{at}.clients: more than {MAX_PROFILE_CLIENTS} clients in the profile")
        macs: set[tuple[int, ...]] = set()
        for j, c in enumerate(rows):
            where = f"{at}.clients[{j}]"
            if not isinstance(c, dict):
                raise ProfileError(f"{where}: an object")
            mac = c.get("mac")
            if not (isinstance(mac, list) and len(mac) == MAC_BYTES and all(_int(b, 0, 255) for b in mac)):
                raise ProfileError(f"{where}.mac: {MAC_BYTES} integers 0..255")
            if tuple(mac) in macs:
                raise ProfileError(f"{where}.mac: {mac} appears twice in the namespace")
            macs.add(tuple(mac))
            if "plugs" in c:
                _plugs(c["plugs"], f"{where}.plugs", names)
                if not has_mdns:
                    _refuse_plugs(c["plugs"], f"{where}.plugs", {"mdns"}, MDNS_WHY)
    if len(json.dumps(obj)) > MAX_RESULT:
        raise ProfileError(f"profile: serialised size over {MAX_RESULT // (1024 * 1024)} MiB")
    return ProfileFacts(namespaces=len(spaces), clients=clients, plugins=sorted(names),
                        vports=sorted({k[0] for k in seen}))
