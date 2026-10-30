"""scripts/install_smoke.py: the wheel check, the built environments and the stop-before-uv guard."""
import importlib.util
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest
from support import ROOT

SCRIPT = ROOT / "scripts" / "install_smoke.py"


@pytest.fixture(scope="module")
def smoke() -> ModuleType:
    """The script loaded as a module; a missing script fails each test rather than the collection."""
    spec = importlib.util.spec_from_file_location("install_smoke", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TRACKED = ["trex_web_gui/__init__.py", "trex_web_gui/studio/templates/LLDP.trp"]
Member = tuple[str, int, bytes]


def good_members() -> list[Member]:
    """The members of a wheel that `check_wheel` accepts for TRACKED."""
    return [
        ("trex_web_gui/__init__.py", 0o644, b""),
        ("trex_web_gui/studio/templates/LLDP.trp", 0o644, b"{}"),
        ("trex_web_gui/static/index.html", 0o644, b"<script>trex-web-gui.theme</script>"),
        ("trex_web_gui/static/assets/app.js", 0o644, b""),
        ("trex_web_gui-1.0.0.dist-info/licenses/LICENSE", 0o644, b""),
        ("trex_web_gui-1.0.0.dist-info/licenses/NOTICE", 0o644, b""),
    ]


def make_wheel(path: Path, members: list[Member]) -> Path:
    """Writes a zip at `path` holding the members with their Unix modes."""
    with zipfile.ZipFile(path, "w") as z:
        for name, mode, data in members:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | mode) << 16
            z.writestr(info, data)
    return path


def problems(smoke: ModuleType, tmp_path: Path, members: list[Member],
             tracked: list[str] = TRACKED) -> list[str]:
    """`check_wheel` over a synthetic wheel."""
    return smoke.check_wheel(make_wheel(tmp_path / "w.whl", members), tracked)


def test_find_wheel_picks_the_project_version(smoke: ModuleType, tmp_path: Path) -> None:
    for v in ("0.9", "1.0.0rc1"):
        (tmp_path / f"trex_web_gui-{v}-py3-none-any.whl").write_bytes(b"")
    assert smoke.find_wheel(tmp_path, "1.0.0rc1").name == "trex_web_gui-1.0.0rc1-py3-none-any.whl"


def test_find_wheel_missing_is_an_error(smoke: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "trex_web_gui-0.9-py3-none-any.whl").write_bytes(b"")
    with pytest.raises(smoke.SmokeError, match="make wheel"):
        smoke.find_wheel(tmp_path, "1.0.0rc1")


def test_check_wheel_accepts_a_good_wheel(smoke: ModuleType, tmp_path: Path) -> None:
    assert problems(smoke, tmp_path, good_members()) == []


def test_check_wheel_refuses_an_untracked_file(smoke: ModuleType, tmp_path: Path) -> None:
    got = problems(smoke, tmp_path, [*good_members(), ("trex_web_gui/stray.py", 0o644, b"")])
    assert len(got) == 1 and "trex_web_gui/stray.py" in got[0]


def test_check_wheel_refuses_a_missing_tracked_file(smoke: ModuleType, tmp_path: Path) -> None:
    got = problems(smoke, tmp_path, good_members(), [*TRACKED, "trex_web_gui/cli.py"])
    assert len(got) == 1 and "trex_web_gui/cli.py" in got[0]


def test_check_wheel_refuses_a_shared_object(smoke: ModuleType, tmp_path: Path) -> None:
    got = problems(smoke, tmp_path, [*good_members(), ("trex_web_gui/x.so", 0o644, b"")],
                   [*TRACKED, "trex_web_gui/x.so"])
    assert len(got) == 1 and "x.so" in got[0]


def test_check_wheel_refuses_a_versioned_shared_object(smoke: ModuleType, tmp_path: Path) -> None:
    got = problems(smoke, tmp_path, [*good_members(), ("trex_web_gui/libx.so.1.2", 0o644, b"")],
                   [*TRACKED, "trex_web_gui/libx.so.1.2"])
    assert len(got) == 1 and "libx.so.1.2" in got[0]


def test_check_wheel_refuses_bytecode(smoke: ModuleType, tmp_path: Path) -> None:
    pyc = "trex_web_gui/__pycache__/a.cpython-312.pyc"
    got = problems(smoke, tmp_path, [*good_members(), (pyc, 0o644, b"")], [*TRACKED, pyc])
    assert got and all("pyc" in p or "__pycache__" in p for p in got)


def test_check_wheel_refuses_a_missing_spa(smoke: ModuleType, tmp_path: Path) -> None:
    members = [m for m in good_members() if not m[0].startswith("trex_web_gui/static/")]
    got = problems(smoke, tmp_path, members)
    assert any("index.html" in p for p in got) and any("assets" in p for p in got)


def test_check_wheel_refuses_a_private_file_mode(smoke: ModuleType, tmp_path: Path) -> None:
    members = [("trex_web_gui/__init__.py", 0o600, b""), *good_members()[1:]]
    got = problems(smoke, tmp_path, members)
    assert len(got) == 1 and "600" in got[0]


def test_check_wheel_refuses_missing_licence_files(smoke: ModuleType, tmp_path: Path) -> None:
    members = [m for m in good_members() if "licenses" not in m[0]]
    got = problems(smoke, tmp_path, members)
    assert any("LICENSE" in p for p in got) and any("NOTICE" in p for p in got)


def test_tool_env_points_every_uv_path_under_root(smoke: ModuleType, tmp_path: Path) -> None:
    env = smoke.tool_env(tmp_path, "/usr/bin")
    uv = {k: v for k, v in env.items() if k.startswith("UV_TOOL_")}
    assert set(uv) == {"UV_TOOL_DIR", "UV_TOOL_BIN_DIR"}
    assert all(Path(v).is_relative_to(tmp_path) for v in uv.values())
    assert env["UV_PYTHON_DOWNLOADS"] == "never"
    assert env["HOME"] == os.environ["HOME"]


def test_serve_env_is_scratch_only(smoke: ModuleType, tmp_path: Path) -> None:
    env = smoke.serve_env(tmp_path, "/usr/bin", "t" * 32)
    assert set(env) == {"PATH", "HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "TREX_WEB_GUI_TOKEN"}
    for k in ("HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        assert Path(env[k]).is_relative_to(tmp_path)


def test_outside_snapshot_detects_a_new_file(smoke: ModuleType, tmp_path: Path) -> None:
    d = tmp_path / "bin"
    d.mkdir()
    (d / "a").write_text("x")
    missing = tmp_path / "missing"
    before = smoke.outside_snapshot([d, missing])
    assert before[str(missing)] == ()
    assert smoke.outside_snapshot([d, missing]) == before
    (d / "b").write_text("y")
    assert smoke.outside_snapshot([d, missing]) != before


def test_run_stops_before_uv_when_the_wheel_is_missing(tmp_path: Path) -> None:
    stub = tmp_path / "stub"
    stub.mkdir()
    calls = tmp_path / "calls.log"
    uv = stub / "uv"
    uv.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    uv.chmod(0o755)
    dist = tmp_path / "dist"
    dist.mkdir()
    env = {"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "run", "--pythons", "3.12",
                        "--dist", str(dist)], env=env, check=False, capture_output=True, text=True)
    assert r.returncode == 1
    assert "make wheel" in r.stdout + r.stderr
    assert not calls.exists()


@pytest.mark.parametrize("stdout", ["", "echo 'Open: http://127.0.0.1:1/wrong'"], ids=["silent", "wrong-url"])
def test_run_reaps_a_serve_that_never_gets_going(smoke: ModuleType, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    """A serve that prints no Open line, or a wrong one, makes run() report FAIL and leaves no child."""
    stub = tmp_path / "stub"
    stub.mkdir()
    uv = stub / "uv"
    uv.write_text(f'#!/bin/sh\n[ "$1 $2" = "tool dir" ] && echo {tmp_path}/tooldir\nexit 0\n')
    uv.chmod(0o755)
    pidfile = tmp_path / "serve.pid"

    def fake_install(root: Path, python: str, wheel: Path) -> float:
        """Stands in for `uv tool install`: a trex-web-gui that records its pid, prints `stdout`, and
        waits."""
        (root / "tools").mkdir(parents=True)
        (root / "bin").mkdir()
        exe = root / "bin" / "trex-web-gui"
        exe.write_text(f"#!/bin/sh\necho $$ > {pidfile}\n{stdout}\nexec sleep 300\n")
        exe.chmod(0o755)
        return 0.0

    dist = tmp_path / "dist"
    dist.mkdir()
    version = smoke.project_version(ROOT / "backend" / "pyproject.toml")
    (dist / f"trex_web_gui-{version}-py3-none-any.whl").write_bytes(b"")
    monkeypatch.setenv("PATH", f"{stub}:{os.environ['PATH']}")
    monkeypatch.setattr(smoke, "uv_install", fake_install)
    monkeypatch.setattr(smoke, "check_wheel", lambda wheel, tracked: [])
    monkeypatch.setattr(smoke, "tracked_files", lambda: [])
    monkeypatch.setattr(smoke, "WAIT", 2.0, raising=False)
    assert smoke.run(["3.12"], dist) == 1
    assert "FAIL" in next(dist.glob("install-smoke-*.log")).read_text()
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
