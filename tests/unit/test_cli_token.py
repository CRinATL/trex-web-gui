"""The per-launch token: its source, its cookie name, the printed URL and the access-log redaction."""
import logging

import pytest
from trex_web_gui import cli
from trex_web_gui.api.auth import (
    MIN_ENV_TOKEN,
    RedactLoginToken,
    cookie_name,
    login_url,
    new_token,
    token_from_env_or_new,
)

LONG = "a-valid_token-0123456789"


def test_token_from_env_wins_and_is_popped() -> None:
    env = {"TREX_WEB_GUI_TOKEN": LONG, "OTHER": "x"}
    assert token_from_env_or_new(env) == LONG
    assert "TREX_WEB_GUI_TOKEN" not in env and env == {"OTHER": "x"}


def test_short_env_token_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    env = {"TREX_WEB_GUI_TOKEN": "x" * (MIN_ENV_TOKEN - 1)}
    with pytest.raises(SystemExit) as e:
        token_from_env_or_new(env)
    assert e.value.code == 2
    assert "TREX_WEB_GUI_TOKEN is too short (minimum 16 characters)" in capsys.readouterr().err
    assert "TREX_WEB_GUI_TOKEN" not in env          # popped even when refused


@pytest.mark.parametrize("bad", ["a;b" + "c" * 20, "tok en" + "c" * 20, "tok=en" + "c" * 20, "é" * 20,
                                 "a,b" + "c" * 20])
def test_env_token_outside_the_urlsafe_set_exits_2(bad: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        token_from_env_or_new({"TREX_WEB_GUI_TOKEN": bad})
    assert e.value.code == 2
    assert "TREX_WEB_GUI_TOKEN may contain only" in capsys.readouterr().err


@pytest.mark.parametrize("env", [{}, {"TREX_WEB_GUI_TOKEN": ""}])
def test_empty_or_absent_env_generates(env: dict[str, str]) -> None:
    assert len(token_from_env_or_new(env)) == 43


def test_generated_token_is_43_chars_urlsafe() -> None:
    t = new_token()
    assert len(t) == 43 and all(c.isalnum() or c in "-_" for c in t)
    assert new_token() != t


def test_cookie_name_is_per_token() -> None:
    a, b = cookie_name("t" * 20), cookie_name("u" * 20)
    assert a != b and a == cookie_name("t" * 20)
    assert a.startswith("trex_web_gui_") and len(a) == len("trex_web_gui_") + 8


def test_login_url_format() -> None:
    assert login_url("127.0.0.1", 8765, "tok") == "http://127.0.0.1:8765/api/auth/login?token=tok"
    assert login_url("127.0.0.1", cli.DEV_PORT, "tok") == f"http://127.0.0.1:{cli.DEV_PORT}/api/auth/login?token=tok"


def _line(path: str) -> str:
    """The access-log message of a 303 GET of path after RedactLoginToken; asserts the record is kept."""
    r = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                          ("127.0.0.1:1", "GET", path, "1.1", 303), None)
    assert RedactLoginToken().filter(r) is True
    return r.getMessage()


@pytest.mark.parametrize("path, shown", [
    ("/api/auth/login?token=abc", "/api/auth/login?token=<redacted>"),
    ("/api/auth/login?x=1&token=abc&y=2", "/api/auth/login?x=1&token=<redacted>&y=2"),
    ("/api/auth/login?token=abc&x=1", "/api/auth/login?token=<redacted>&x=1"),
    ("/api/auth/login?token=a&token=b", "/api/auth/login?token=<redacted>&token=<redacted>"),
    ("/api/auth/login?%74oken=abc", "/api/auth/login?%74oken=<redacted>"),
    ("/api/auth/login", "/api/auth/login"),
    ("/api/auth/login?xtoken=abc", "/api/auth/login?xtoken=abc"),
    ("/api/about?token=abc", "/api/about?token=abc"),
])
def test_access_log_filter_redacts_login_query(path: str, shown: str) -> None:
    msg = _line(path)
    assert f"GET {shown} HTTP" in msg
    if "login?" in shown and "token" in shown and "xtoken" not in shown:
        assert "abc" not in msg
