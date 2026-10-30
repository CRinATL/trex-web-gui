import json
import re
import subprocess
import sys

import pytest
from support import FIXTURES
from trex_web_gui.validation import MULT, NAME_RE, check_code_safe, check_duration, check_host, check_ports


@pytest.mark.parametrize("v", ["1", "1.5", "10kpps", "5pps", "100mbps", "10gbps", "1.5kbpsl1", "50%", "0"])
def test_mult_regex_unchanged(v: str) -> None:
    assert re.fullmatch(MULT, v)


@pytest.mark.parametrize("v", ["", "1.", ".5", "-1", "10x", "10kpps+", "1\n", "10 pps", "kpps", "10kpps\n"])
def test_mult_regex_refuses(v: str) -> None:
    assert not re.fullmatch(MULT, v)


def test_check_duration() -> None:
    assert check_duration(-1) == -1 and check_duration(0.5) == 0.5 and check_duration(604800) == 604800
    for bad in (0, -2, 604800.1, float("nan")):
        with pytest.raises(ValueError, match="duration"):
            check_duration(bad)


def test_check_host() -> None:
    for ok in ("127.0.0.1", "trex.lab.example", "localhost", "::1", "fe80::1", "2001:db8::2", "a" * 253):
        assert check_host(ok) == ok
    for bad in ("", "a" * 254, "a b", "a\tb", "a\n", "h/x", "u@h", "[::1]", "[::1", "::1]", "host:80", "::g",
                "1:2:3:4:5:6:7:8:9", "fe80::1%eth0", "h\u00e9", "a#b", "a?b"):
        with pytest.raises(ValueError):
            check_host(bad)


def test_check_ports() -> None:
    assert check_ports([0, 1]) == [0, 1]
    for bad in ([1, 1], [-1]):
        with pytest.raises(ValueError, match="unique"):
            check_ports(bad)


def test_store_imports_without_api() -> None:
    code = ("import sys; import trex_web_gui.store.test_setups; "
            "sys.exit(1 if 'trex_web_gui.api.app' in sys.modules else 0)")
    r = subprocess.run([sys.executable, "-I", "-c", code], check=False, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr


NAME_ACCEPT = ["udp_1", "a b", "x" * 80, "#&()[]~"]
NAME_REFUSE = ["", "x" * 81, "a'b", 'a"b', "a\\b", "a\tb", "a\nb", "café"]


@pytest.mark.parametrize("v", NAME_ACCEPT)
def test_name_re_table(v: str) -> None:
    """The shared name table (Task 9's streamForm.test.ts runs the same rows): accepted."""
    assert NAME_RE.fullmatch(v)


@pytest.mark.parametrize("v", NAME_REFUSE)
def test_name_re_table_refuses(v: str) -> None:
    """The shared name table: refused."""
    assert not NAME_RE.fullmatch(v)


def test_check_code_safe_accepts_the_recorded_streams_and_refuses_crafted_text() -> None:
    """Every stream of the Task 1 fixtures passes; crafted names and VM strings name their field."""
    streams = json.loads((FIXTURES / "profiles/two_streams.json").read_text())
    check_code_safe(streams)
    ok = {"vm": {"instructions": [{"type": "flow_var", "name": "a.b", "op": "inc", "next_var": None,
                                   "init_value": 1, "value_list": [1, 2]}], "split_by_var": ""}}
    check_code_safe([ok])
    bad: list[tuple[dict[str, object], str]] = [
        ({"name": "x'); __import__('os').system('id'); ('"}, "stream 1 name"),
        ({"name": "a\nb"}, "stream 1 name"),
        ({"name": 0}, "stream 1 name"),
        ({"name": True}, "stream 1 name"),
        ({"next": "a'b"}, "stream 1 next"),
        ({"vm": {"instructions": [{"type": "flow_var", "name": "a'b"}]}}, "stream 1 vm.instructions[0].name"),
        ({"vm": {"instructions": [{"type": "flow_var", "op": "inc\n"}]}}, "stream 1 vm.instructions[0].op"),
        ({"vm": {"instructions": [], "split_by_var": "a'b"}}, "stream 1 vm.split_by_var"),
    ]
    for stream, where in bad:
        with pytest.raises(ValueError, match=re.escape(where) + ":"):
            check_code_safe([ok, stream])
