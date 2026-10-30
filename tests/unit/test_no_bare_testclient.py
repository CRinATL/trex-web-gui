"""Every TestClient under tests/ goes through client(), authed() or anon(), so none is silently
unauthenticated (or silently authenticated where the test means to be anonymous)."""
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent
PATTERN = "TestClient" + "("                                  # split so this file does not match itself
ALLOWED = {TESTS / "support.py", Path(__file__).resolve()}      # the helper module, and this file's pattern


def offenders(root: Path) -> list[str]:
    """"path:line" of each bare TestClient call in the .py files under root, bar support.py and this file."""
    return [f"{p.relative_to(root)}:{n}" for p in sorted(root.rglob("*.py")) if p.resolve() not in ALLOWED
            for n, line in enumerate(p.read_text().splitlines(), 1) if PATTERN in line]


def test_every_testclient_site_uses_a_helper() -> None:
    assert offenders(TESTS) == []


def test_a_bare_site_is_found() -> None:
    """Negative control: the grep does fail on a bare site."""
    before = offenders(TESTS)
    stray = TESTS / "unit" / "_stray_bare_testclient.py"
    stray.write_text("c = " + PATTERN + "app, base_url='http://127.0.0.1')\n")
    try:
        found = [o for o in offenders(TESTS) if o not in before]
    finally:
        stray.unlink()
    assert len(found) == 1 and found[0].startswith("unit/_stray_bare")
