"""The backend process never imports the EMU client, profile or API (spec §4.6; ruling R25): profile code runs
only in the worker. The vendored STL client's parsing_opts imports the validator, so three modules are the
named exception."""
import ast
import re
import subprocess
import sys

from support import FIXTURES, ROOT

PKG = ROOT / "backend/trex_web_gui"
WORKER = PKG / "trexapi/profile_worker.py"
# Ways of naming trex.emu in source: import, from-import, `from trex import emu`, import_module("trex.emu").
IMPORT = re.compile(r"^\s*(import|from)\s+trex\.emu\b|^\s*from\s+trex\s+import\s+[^#\n]*\bemu\b"
                    r"|import_module\(\s*['\"]trex\.emu", re.M)
# trex/utils/parsing_opts.py:5 "from trex.emu.trex_emu_validator import" pulls these in with trex.stl.api.
ALLOWED = {"trex.emu", "trex.emu.trex_emu_validator", "trex.emu.trex_emu_conversions"}

# A fresh `python -I` interpreter, so other tests' imports cannot pollute sys.modules: import the app, load
# the vendored STL client as every connection does, run a real EMU profile load, print what trex.emu holds.
CODE = f"""
import asyncio, sys
sys.path.insert(0, {str(ROOT / "backend")!r})
from trex_web_gui.api import app
from trex_web_gui.trexapi import loader, profiles
loader.load()
lp = asyncio.run(profiles.load_emu_profile({str(FIXTURES / "profiles" / "emu_two_ns.py")!r}, []))
assert lp.clients == 6
print(sorted(m for m in sys.modules if m == 'trex.emu' or m.startswith('trex.emu.')))
print('emu_two_ns' in sys.modules)
"""


def test_a_loaded_backend_holds_only_the_validator_modules_of_trex_emu() -> None:
    """After the app is built, the STL loader has run and a real EMU profile has loaded, only the three
    modules parsing_opts imports are in sys.modules, and the profile's own module is absent."""
    r = subprocess.run([sys.executable, "-I", "-B", "-c", CODE], check=False, capture_output=True, text=True,
                       timeout=120, env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"})
    assert r.returncode == 0, r.stderr
    held, profile_module = r.stdout.strip().splitlines()
    extra = sorted(set(ast.literal_eval(held)) - ALLOWED)       # a list of str printed by CODE above
    assert extra == [], (f"the backend process imported {extra}; only {sorted(ALLOWED)} may be there "
                         "(the vendored parsing_opts imports the validator with trex.stl.api)")
    assert profile_module == "False"


def test_no_backend_source_imports_trex_emu_outside_the_worker() -> None:
    """Statically: only profile_worker.py names trex.emu in an import."""
    offenders = [str(f.relative_to(ROOT)) for f in PKG.rglob("*.py")
                 if "_vendor" not in f.parts and f != WORKER and IMPORT.search(f.read_text())]
    assert offenders == []
    assert IMPORT.search(WORKER.read_text())              # the check sees the worker's import


def test_the_source_scan_catches_every_spelling() -> None:
    """The regex itself: five spellings match, an unrelated name does not."""
    for line in ("import trex.emu.api", "from trex.emu.api import x", "from trex import emu",
                 "from trex import stl, emu", 'importlib.import_module("trex.emu.api")'):
        assert IMPORT.search(line), line
    assert not IMPORT.search("from trex import stl")
