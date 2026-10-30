"""Where the running backend finds its SPA, and whether it runs from a wheel or a checkout (P6, D11)."""
import importlib.resources
from pathlib import Path
from typing import Literal

import trex_web_gui


def package_static_dir() -> Path:
    """The SPA directory inside the installed package: `trex_web_gui/static` (what the wheel carries)."""
    return Path(str(importlib.resources.files("trex_web_gui") / "static"))


def source_static_dir() -> Path:
    """The checkout's built SPA, `frontend/dist`, two levels above this file's directory."""
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def resolve_static(package: Path | None = None, source: Path | None = None) -> Path | None:
    """The first of `package` and `source` (defaults: the functions above) with an index.html, else None."""
    for d in (package if package is not None else package_static_dir(),
              source if source is not None else source_static_dir()):
        if (d / "index.html").is_file():
            return d
    return None


def install_kind(package_dir: Path | None = None) -> Literal["wheel", "source"]:
    """"source" when a `pyproject.toml` sits beside the `trex_web_gui` package directory (a checkout's
    `backend/`), else "wheel"."""
    here = package_dir or Path(trex_web_gui.__file__).parent
    return "source" if (here.parent / "pyproject.toml").exists() else "wheel"
