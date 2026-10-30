"""The committed vendored tree, checked on every Python the project supports (make check-py runs this file;
it deselects test_vendor_patches.py, whose builds need a trex-core checkout)."""
from support import VENDOR, warnings_in


def test_committed_vendor_tree_compiles_without_syntax_warnings() -> None:
    assert warnings_in(VENDOR / "trex") == 0
