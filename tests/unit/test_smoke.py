import tomllib
from pathlib import Path

import trex_web_gui

PYPROJECT = Path(__file__).resolve().parents[2] / "backend" / "pyproject.toml"


def test_version_is_set() -> None:
    expected = tomllib.loads(PYPROJECT.read_text())["project"]["version"]
    assert trex_web_gui.__version__ == expected
