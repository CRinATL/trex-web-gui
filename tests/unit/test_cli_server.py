"""`trex-web-gui serve --server host[:sync[:async]]`: the argparse type and its refusals."""
import argparse

import pytest
from trex_web_gui import cli


@pytest.mark.parametrize(("text", "host", "ports"), [
    ("127.0.0.1", "127.0.0.1", None),
    ("127.0.0.1:4601:4600", "127.0.0.1", (4601, 4600)),
    ("lab1:4601", "lab1", (4601, 4600)),
    ("[::1]:4601:4600", "::1", (4601, 4600)),
    ("[::1]", "::1", None),
    ("::1", "::1", None),
    ("fe80::1", "fe80::1", None),
])
def test_parse_server(text: str, host: str, ports: tuple[int, int] | None) -> None:
    assert cli.parse_server(text) == (host, ports)


@pytest.mark.parametrize("text", [
    "h:0", "h:70000", "h:4601:x", ":4601", "h:1", "[::1]:", "[::1", "host:4601:4600:1", "h::4601",
    "h: 4601", "h:4_601", "h:+4601", " h", "h ", "h@x", "a/b", "a/b:4601", "[a/b]:4601",
])
def test_parse_server_rejects(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_server(text)


def test_bad_server_exits_2() -> None:
    with pytest.raises(SystemExit) as e:
        cli.build_parser().parse_args(["serve", "--server", "h:0"])
    assert e.value.code == 2


@pytest.mark.parametrize(("text", "want"), [
    ("h", ("h", 4500)),
    ("h:4600", ("h", 4600)),
    ("10.0.0.5:65535", ("10.0.0.5", 65535)),
    ("[::1]:4600", ("::1", 4600)),
    ("[::1]", ("::1", 4500)),
    ("::1", ("::1", 4500)),
])
def test_monitor_flag_parsing(text: str, want: tuple[str, int]) -> None:
    assert cli.parse_monitor(text) == want


@pytest.mark.parametrize("text", [
    "h:0", "h:70000", "h:x", "h:", ":4500", "", "[::1", "[::1]x", "[::1]:", "a b", "h/x",
    "1:2:3:4:5:6:7:8:9", "-x", "_x", ".a", "-", ".", "..", "a..b", "a.-b", "a-.b",
    "h:4_500", " h",
])
def test_monitor_flag_rejects(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_monitor(text)


def test_monitor_flags_reach_the_parser() -> None:
    a = cli.build_parser().parse_args(["serve", "--monitor", "h:4600", "--monitor-only"])
    assert a.monitor == ("h", 4600) and a.monitor_only is True
    b = cli.build_parser().parse_args(["serve"])
    assert b.monitor is None and b.monitor_only is False


def test_daemon_port_reaches_hello(monkeypatch: pytest.MonkeyPatch) -> None:
    """--daemon-port (default 8090) is handed to create_app, which puts {host, port} in hello; the host is
    the --server host or 127.0.0.1."""
    assert cli.build_parser().parse_args(["serve"]).daemon_port == 8090
    assert cli.build_parser().parse_args(["serve", "--daemon-port", "8190"]).daemon_port == 8190
    for bad in ("0", "65536", "x"):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["serve", "--daemon-port", bad])
    import uvicorn
    from trex_web_gui.api import app as app_mod
    made: list[dict] = []
    monkeypatch.setattr(app_mod, "create_app", lambda *a, **kw: made.append({"args": a, **kw}))
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)
    cli.main(["serve", "--daemon-port", "8190", "--server", "lab1:4601"])
    cli.main(["serve"])
    assert made[0]["daemon_port"] == 8190 and made[0]["args"][1] == "lab1"
    assert made[1]["daemon_port"] == 8090 and made[1]["args"][1] is None
