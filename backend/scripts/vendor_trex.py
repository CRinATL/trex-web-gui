"""Vendor the pinned TRex Python client into trex_web_gui/_vendor and apply vendor/patches (plan §4.5).

The source clone has no git history, so the pin records the upstream SHA as given plus a sha256 over
the copied source files; re-running on an unchanged clone yields byte-identical output."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

TREX_SUBDIR = "scripts/automation/trex_control_plane/interactive/trex"
STL_LIB_SUBDIR = "scripts/automation/trex_control_plane/interactive/trex_stl_lib"
TREX_PARTS = ["__init__.py", "common", "stl", "utils", "scapy_server", "astf", "emu"]
EXT_SUBDIR = "scripts/external_libs"
EXT_LIBS = ["texttable-0.8.4", "scapy-2.4.3", "simpy-3.0.10", "dpkt-1.9.1", "repoze"]
# Not trex-openssl: GPL-incompatible OpenSSL 1.1 (patch 10). Not pyzmq-ctypes (ships .so files) or
# pyyaml-3.11: the venv supplies zmq and yaml through TREX_SYSTEM_LIBS (patch 01); a wheel holds no .so (P6).
MARKER = ".vendored-by-trex-web-gui"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "unit_tests", "examples")


def tree_sha256(root: Path) -> str:
    """Hex sha256 over every file under `root` (sorted relative path and bytes), so it ignores mtimes."""
    h = hashlib.sha256()
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(root)).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def main() -> int:
    """Rebuild the vendored tree at --out from --src and patch it; replaces a marked --out, refuses another.

    Also writes TREX_PIN and MANIFEST.sha256 in --out and the --pin file when given."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--patches", type=Path, required=True)
    ap.add_argument("--pin", type=Path)
    ap.add_argument("--skip-patch", action="append", default=[], help="NN prefix; for negative controls")
    a = ap.parse_args()

    if a.out.exists():
        if not (a.out / MARKER).exists():
            sys.exit(f"refusing to replace {a.out}: no {MARKER} marker")
        shutil.rmtree(a.out)
    a.out.mkdir(parents=True)
    (a.out / MARKER).write_text("generated; do not edit, re-run backend/scripts/vendor_trex.py\n")

    (a.out / "trex").mkdir()
    for part in TREX_PARTS:
        s, d = a.src / TREX_SUBDIR / part, a.out / "trex" / part
        if s.is_dir():
            shutil.copytree(s, d, ignore=IGNORE)
        else:
            shutil.copy2(s, d)
    # v3.08 profiles start with `from trex_stl_lib.api import *` (2-file compat package, Deviation 4)
    shutil.copytree(a.src / STL_LIB_SUBDIR, a.out / "trex_stl_lib", ignore=IGNORE)
    for lib in EXT_LIBS:
        shutil.copytree(a.src / EXT_SUBDIR / lib, a.out / "external_libs" / lib, ignore=IGNORE)
    src_sha = tree_sha256(a.out)

    applied = []
    for p in sorted(a.patches.glob("*.patch")):
        if p.name[:2] in a.skip_patch:
            continue
        subprocess.run(["patch", "-p1", "--forward", "--batch", "--no-backup-if-mismatch", "-s",
                        "-d", str(a.out), "-i", str(p.resolve())], check=True)
        applied.append(p.name)

    pin = {"upstream_sha": a.sha, "source_tree_sha256": src_sha, "patches": applied,
           "skipped": sorted(a.skip_patch)}
    (a.out / "TREX_PIN").write_text(json.dumps(pin, indent=1) + "\n")
    if a.pin:
        a.pin.write_text(json.dumps(pin, indent=1) + "\n")
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(a.out)}"
             for f in sorted(a.out.rglob("*")) if f.is_file() and f.name != "MANIFEST.sha256"]
    (a.out / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
    print(f"vendored {len(lines)} files, {len(applied)} patches, source sha256 {src_sha[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
