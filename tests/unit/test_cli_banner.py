"""The start-up banner (D12): version, STL API, client pin, and the two URL lines."""
import pytest
import trex_web_gui
from trex_web_gui import cli

TOKEN = "A" * 32


def test_banner_lines_with_a_fixed_token() -> None:
    assert cli.banner_lines("1.0.0rc1", "27e0153b5", 8765, 8765, TOKEN, "u@h") == [
        "trex-web-gui 1.0.0rc1 for STL API 5.1 (TRex v3.08), client pin 27e0153b5, serving on http://127.0.0.1:8765",
        f"Open: http://127.0.0.1:8765/api/auth/login?token={TOKEN}",
        f"From another machine: ssh -N -L 8765:127.0.0.1:8765 u@h, "
        f"then open http://localhost:8765/api/auth/login?token={TOKEN}",
    ]


def test_banner_lines_dev_shows_the_vite_port() -> None:
    """p3c-capture-17: with --dev the first line names the backend port, the other two the Vite port."""
    lines = cli.banner_lines("1.0.0rc1", "27e0153b5", 8765, 5173, TOKEN, "u@h")
    assert lines[0].endswith("serving on http://127.0.0.1:8765")
    assert lines[1].startswith("Open: http://127.0.0.1:5173/")
    assert "-L 5173:127.0.0.1:5173 u@h" in lines[2] and "http://localhost:5173/" in lines[2]


def test_banner_lines_without_a_pin() -> None:
    first = cli.banner_lines("1.0.0rc1", None, 8765, 8765, TOKEN, "u@h")[0]
    assert first == ("trex-web-gui 1.0.0rc1 for STL API 5.1 (TRex v3.08), client pin unreadable, "
                     "serving on http://127.0.0.1:8765")


def test_main_prints_the_banner(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import uvicorn
    from trex_web_gui.api import app as app_mod
    monkeypatch.setattr(app_mod, "create_app", lambda *a, **kw: None)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)
    cli.main(["serve"])
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(
        f"trex-web-gui {trex_web_gui.__version__} for STL API 5.1 (TRex v3.08), client pin ")
    assert out[1].startswith("Open: http://127.0.0.1:8765/api/auth/login?token=")
    assert out[2].startswith("From another machine: ssh -N -L 8765:127.0.0.1:8765 ")
