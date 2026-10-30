"""Hatch build hook (D17): a wheel carries the built SPA as `trex_web_gui/static`; an editable one
needs none."""
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):  # type: ignore[misc]
    """Adds `../frontend/dist` to the wheel, and refuses to build a wheel without it."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """For an editable build adds nothing; otherwise requires `frontend/dist/index.html` (RuntimeError
        naming `make build` when missing) and force-includes the directory as `trex_web_gui/static`."""
        if version == "editable":
            return
        dist = Path(self.root).parent / "frontend" / "dist"
        if not (dist / "index.html").is_file():
            raise RuntimeError(f"{dist}/index.html is missing: run make build (make wheel does) "
                               "before building the wheel")
        build_data["force_include"][str(dist)] = "trex_web_gui/static"
