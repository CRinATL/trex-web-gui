"""Static-directory resolution (trex_web_gui.install) and `trex-web-gui serve` passing it on (P6 Task 3)."""
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import trex_web_gui
from support import ROOT
from trex_web_gui import cli
from trex_web_gui.install import install_kind, package_static_dir, resolve_static


def _spa(d: Path) -> Path:
    """Creates `d` holding an index.html and returns it."""
    d.mkdir(parents=True)
    (d / "index.html").write_text("<html></html>")
    return d


def test_package_static_wins_when_it_has_index_html(tmp_path: Path) -> None:
    package, source = _spa(tmp_path / "package"), _spa(tmp_path / "source")
    assert resolve_static(package, source) == package


def test_source_tree_dist_when_the_package_has_none(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    source = _spa(tmp_path / "source")
    assert resolve_static(package, source) == source


def test_none_when_neither_has_index_html(tmp_path: Path) -> None:
    package, source = tmp_path / "package", tmp_path / "source"
    package.mkdir()
    source.mkdir()
    assert resolve_static(package, source) is None


def test_default_package_static_is_trex_web_gui_static() -> None:
    assert package_static_dir() == Path(trex_web_gui.__file__).parent / "static"


def test_install_kind_is_source_in_this_checkout() -> None:
    assert install_kind() == "source"


def test_install_kind_is_wheel_without_a_pyproject_beside_the_package(tmp_path: Path) -> None:
    pkg = tmp_path / "site-packages" / "trex_web_gui"
    pkg.mkdir(parents=True)
    assert install_kind(pkg) == "wheel"
    (pkg.parent / "pyproject.toml").write_text("")
    assert install_kind(pkg) == "source"


def test_serve_passes_the_resolved_static_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import uvicorn
    from trex_web_gui.api import app as app_module

    seen: list[Any] = []

    def fake_create_app(data_dir: Path, hint: Any, static_dir: Path | None, **kw: Any) -> object:
        seen.append(static_dir)
        return object()

    monkeypatch.setattr(app_module, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(cli, "resolve_static", lambda: tmp_path)
    data = str(tmp_path / "data")
    assert cli.main(["serve", "--data-dir", data]) == 0
    assert cli.main(["serve", "--dev", "--data-dir", data]) == 0
    assert seen == [tmp_path, None]


def test_the_console_script_ignores_ctrl_c_once_the_server_has_stopped() -> None:
    """A Ctrl+C that lands while Python runs its exit handlers (logging.shutdown) printed a KeyboardInterrupt
    traceback: `uv run` forwards each Ctrl+C again about 0.2 s later, and under load one arrived after
    uvicorn had returned (12 pytest workers, 2026-10-28). The console script is entry(), which leaves SIGINT
    ignored once main() is done, whatever main() did; main() itself still restores the handler it found."""
    code = ("import signal, sys\n"
            "from trex_web_gui import cli\n"
            "cli.main = lambda argv=None: 7\n"
            "rc = cli.entry()\n"
            "print(rc, signal.getsignal(signal.SIGINT) is signal.SIG_IGN)\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT / "backend",
                       timeout=60)
    assert r.stdout.split() == ["7", "True"], r.stderr
    assert 'trex-web-gui = "trex_web_gui.cli:entry"' in (ROOT / "backend" / "pyproject.toml").read_text()
