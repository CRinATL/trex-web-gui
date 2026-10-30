"""The wheel's inputs: build hook (D17), pyproject keys, dependency bounds (D9), metadata symlinks (D4)."""
import importlib.util
import re
import sys
import tomllib
import types
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.version import Version
from support import ROOT

PYPROJECT = ROOT / "backend" / "pyproject.toml"
RUNTIME = {"fastapi", "uvicorn", "websockets", "pyzmq", "pyyaml", "tomli-w"}


def load_hook(monkeypatch: pytest.MonkeyPatch) -> Any:
    """backend/hatch_build.py, imported over a stub hatchling (not installed in the dev venv)."""
    class BuildHookInterface:
        def __init__(self, root: str) -> None:
            """Holds the project root, as hatchling does."""
            self.root = root

    mods = {}
    for name in ("hatchling", "hatchling.builders", "hatchling.builders.hooks",
                 "hatchling.builders.hooks.plugin", "hatchling.builders.hooks.plugin.interface"):
        mods[name] = types.ModuleType(name)
    mods["hatchling.builders.hooks.plugin.interface"].BuildHookInterface = BuildHookInterface  # type: ignore[attr-defined]
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("hatch_build_under_test",
                                                  ROOT / "backend" / "hatch_build.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_hook_adds_the_spa_to_a_wheel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("x")
    (tmp_path / "backend").mkdir()
    data: dict[str, Any] = {"force_include": {}}
    load_hook(monkeypatch).CustomBuildHook(str(tmp_path / "backend")).initialize("standard", data)
    assert data["force_include"] == {str(dist): "trex_web_gui/static"}


def test_build_hook_refuses_a_wheel_without_a_spa(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    data: dict[str, Any] = {"force_include": {}}
    with pytest.raises(RuntimeError, match="make build"):
        load_hook(monkeypatch).CustomBuildHook(str(tmp_path / "backend")).initialize("standard", data)
    assert data["force_include"] == {}


def test_build_hook_adds_nothing_to_an_editable_install(monkeypatch: pytest.MonkeyPatch,
                                                        tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    data: dict[str, Any] = {"force_include": {}}
    load_hook(monkeypatch).CustomBuildHook(str(tmp_path / "backend")).initialize("editable", data)
    assert data["force_include"] == {}


def test_pyproject_enables_the_custom_hook() -> None:
    cfg = tomllib.loads(PYPROJECT.read_text())
    assert cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["hooks"]["custom"] == {}
    assert "force-include" not in cfg["tool"]["hatch"]["build"]["targets"]["wheel"]


def bound_problems(deps: list[str], locked: dict[str, str]) -> list[str]:
    """Each of the six runtime dependencies needs a >= bound that is not above its locked version."""
    out = []
    reqs = {Requirement(d).name: Requirement(d) for d in deps}
    for name in sorted(RUNTIME):
        req = reqs.get(name)
        lows = [s for s in req.specifier if s.operator == ">="] if req else []
        if not lows:
            out.append(f"{name}: no >= bound")
        elif Version(lows[0].version) > Version(locked[name]):
            out.append(f"{name}: bound {lows[0].version} is above the locked {locked[name]}")
    return out


def test_dependency_lower_bounds_not_above_the_lock() -> None:
    deps = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    lock = tomllib.loads((ROOT / "backend" / "uv.lock").read_text())
    locked = {p["name"]: p["version"] for p in lock["package"]}
    assert bound_problems(deps, locked) == []
    # negative control: a test-local copy with an impossible bound is reported
    bad = [d if not d.startswith("fastapi") else "fastapi>=999" for d in deps]
    assert bound_problems(bad, locked) == [f"fastapi: bound 999 is above the locked {locked['fastapi']}"]
    assert bound_problems([d for d in deps if not d.startswith("pyzmq")], locked) == ["pyzmq: no >= bound"]


def test_metadata_files_are_symlinks_to_the_repo_root() -> None:
    for name in ("README.md", "LICENSE", "NOTICE"):
        link = ROOT / "backend" / name
        assert link.is_symlink(), name
        assert link.resolve() == (ROOT / name).resolve()
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    assert project["readme"] == "README.md"
    assert project["license-files"] == ["LICENSE", "NOTICE"]


def test_service_unit_sample() -> None:
    text = (ROOT / "docs" / "trex-web-gui.service").read_text()
    assert "ExecStart=%h/.local/bin/trex-web-gui serve" in text.splitlines()
    assert re.search(r"^# ?Environment=DISPLAY=:0$", text, re.M)
    assert "systemctl --user import-environment DISPLAY WAYLAND_DISPLAY" in text


def test_readme_install_section() -> None:
    text = (ROOT / "README.md").read_text()
    assert text.index("## Install") < text.index("## Requirements")
    for needle in ("uv tool install", "journalctl --user -u trex-web-gui", "### Offline host",
                   "--no-index --find-links", "docs/api.md"):
        assert needle in text, needle
