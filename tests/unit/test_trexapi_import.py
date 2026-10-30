import os
import subprocess
import sys
from pathlib import Path

from support import ROOT, pinned_sha

CODE = """
from trex_web_gui import trexapi
trexapi.load(); trexapi.load()
import zmq, yaml, trex.stl.api, trex.scapy_server.scapy_service
print('external_libs' in zmq.__file__, 'external_libs' in yaml.__file__, trexapi.pin()['upstream_sha'])
"""


def _run(tmp_path: Path, vendor: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Runs CODE in a fresh interpreter (backend on PYTHONPATH, -B so no bytecode is written, SyntaxWarning an
    error, 60 s limit); an inherited vendor override is dropped unless vendor is given. A nonzero exit is
    returned, not raised."""
    env = dict(os.environ)
    env.pop("TREX_WEB_GUI_VENDOR_OVERRIDE", None)
    env["PYTHONPATH"] = str(ROOT / "backend")  # the package is not installed; pytest.ini uses pythonpath
    if vendor:
        env["TREX_WEB_GUI_VENDOR_OVERRIDE"] = str(vendor)
    return subprocess.run([sys.executable, "-B", "-X", f"pycache_prefix={tmp_path / 'pyc'}",
                           "-W", "error::SyntaxWarning", "-c", CODE],
                          check=False, capture_output=True, text=True, timeout=60, env=env)


def test_load_uses_venv_libs_and_emits_no_syntax_warnings(tmp_path: Path) -> None:
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["False", "False", pinned_sha()]


def test_negative_control_without_patch_04(tmp_path: Path, build_vendor) -> None:
    r = _run(tmp_path, build_vendor(["04"]))
    # -W error turns the SyntaxWarning into SyntaxError
    assert r.returncode != 0 and "invalid escape sequence" in r.stderr
