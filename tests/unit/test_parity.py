"""scripts/parity.py: the v1.0 parity rows from the inventory, the generated doc, report and gate modes."""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from support import ROOT

SCRIPT = ROOT / "scripts" / "parity.py"


def load_script() -> Any:
    """Imports scripts/parity.py; a missing script fails each test, not the collection."""
    spec = importlib.util.spec_from_file_location("parity", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["parity"] = mod
    try:
        spec.loader.exec_module(mod)
    except FileNotFoundError:
        pass
    return mod


parity = load_script()

HEAD = ("| ID | Feature | Legacy source | Current API / note | Verdict | Phase | Was | Status |\n"
        "|---|---|---|---|---|---|---|---|\n")
INVENTORY = (
    "## B0 facts\n\n| ID | Fact | Source | Note |\n|---|---|---|---|\n| B0.1 | Keep | x | y |\n\n"
    "## A\n\n" + HEAD +
    "| A1.1 | Connect | src | api | Keep | P2 | F | built P2 (tested) |\n"
    "| A1.2 | Stats | src | api | Keep+ | P3 | F | partial P3 |\n"
    "| A1.3 | Events | src | api | New | P2 | F | ? |\n"
    "| A1.4 | Split | src | api | Replace | P3 / P8 | F | built P3 |\n"
    "| A1.5 | Dropped | src | api | Drop | P2 | F | n/a |\n"
    "| A1.6 | Later | src | api | Later | P3 | F | deferred |\n"
    "| A1.7 | Far | src | api | Keep | P7 | F | built P7 |\n"
    "## D\n\n| ID | Capability | Source | Verdict | Phase | Was | Status |\n|---|---|---|---|---|---|---|\n"
    "| D1 | Multi | src | New | P2 | = A1.4 | built P2 |\n"
)
GREP = re.compile(r"^\| [ABC][0-9]+(\.[0-9]+)? .*\| (Keep|Keep\+|Replace|New)[^|]*\| P[1-6]", re.M)


def doc_with(evidence: dict[str, str], inventory: str = INVENTORY) -> str:
    """Syncs the inventory into a doc, then fills the named evidence cells."""
    text = parity.sync(inventory, None)
    for id_, ev in evidence.items():
        pattern = rf"^(\| {re.escape(id_)} \|.*)\| *\|$"
        text = re.sub(pattern, lambda m, ev=ev: f"{m[1]}| {ev} |", text, flags=re.M)
    return text


FULL = {"A1.1": "rc1 use, 2026-11-01", "A1.2": "rc1 use, 2026-11-01", "A1.3": "rc1 use, 2026-11-01",
        "A1.4": "rc1 use, 2026-11-01"}


def test_parity_rows_follow_the_bar() -> None:
    rows = parity.parity_rows(INVENTORY)
    assert [(r.id, r.verdict, r.phase, r.status) for r in rows] == [
        ("A1.1", "Keep", "P2", "built"), ("A1.2", "Keep+", "P3", "partial"),
        ("A1.3", "New", "P2", "?"), ("A1.4", "Replace", "P3 / P8", "built")]


def test_count_equals_the_inventory_grep() -> None:
    assert len(parity.parity_rows(INVENTORY)) == len(GREP.findall(INVENTORY))


def test_phase_must_start_with_p1_to_p6() -> None:
    text = INVENTORY + "| A9.9 | Moved | s | a | Keep | P8 (was P3) | F | built |\n"
    assert "A9.9" not in [r.id for r in parity.parity_rows(text)]


def test_sync_keeps_evidence_and_adds_new_rows() -> None:
    old = doc_with({"A1.1": "rc1 use, 2026-11-01"})
    bigger = INVENTORY.replace("## D", "| A1.8 | Added | s | a | Keep | P2 | F | built P2 |\n## D")
    new = parity.sync(bigger, old)
    assert "| A1.1 | Connect | P2 | built | rc1 use, 2026-11-01 |" in new
    assert "| A1.8 | Added | P2 | built |  |" in new


def test_sync_refuses_a_row_gone_from_the_inventory() -> None:
    old = doc_with({})
    smaller = INVENTORY.replace("| A1.2 | Stats | src | api | Keep+ | P3 | F | partial P3 |\n", "")
    with pytest.raises(parity.ParityError, match="A1.2"):
        parity.sync(smaller, old)


def test_report_mode_passes_with_gaps_and_counts_them(tmp_path: Path) -> None:
    rc, lines = parity.check(INVENTORY, doc_with({"A1.1": "rc1 use, 2026-11-01"}), False, tmp_path)
    assert rc == 0
    assert lines[0] == "4 parity rows; 2 built, 1 partial, 1 ?; 3 without evidence"
    assert lines[1:] == ["A1.2", "A1.3", "A1.4"]


def test_gate_mode_fails_on_question_status(tmp_path: Path) -> None:
    rc, lines = parity.check(INVENTORY, doc_with(FULL), True, tmp_path)
    assert rc == 1 and any("A1.3" in line for line in lines)


def test_gate_mode_counts_empty_evidence_but_passes(tmp_path: Path) -> None:
    inv = INVENTORY.replace("| New | P2 | F | ? |", "| New | P2 | F | built P2 |")
    rc, lines = parity.check(inv, doc_with({**FULL, "A1.4": ""}, inv), True, tmp_path)
    assert rc == 0, lines
    assert lines[0].endswith("; 1 without evidence") and lines[1:] == ["A1.4"]


def test_gate_mode_passes_partial_with_evidence(tmp_path: Path) -> None:
    inv = INVENTORY.replace("| New | P2 | F | ? |", "| New | P2 | F | built P2 |")
    rc, _ = parity.check(inv, doc_with(FULL, inv), True, tmp_path)
    assert rc == 0


@pytest.mark.parametrize("gate", [False, True])
def test_missing_evidence_path_fails_in_both_modes(tmp_path: Path, gate: bool) -> None:
    doc = doc_with({**FULL, "A1.1": "`tests/unit/nope.py::test_x`"})
    rc, lines = parity.check(INVENTORY, doc, gate, tmp_path)
    assert rc == 1 and any("tests/unit/nope.py" in line for line in lines)
    (tmp_path / "tests/unit").mkdir(parents=True)
    (tmp_path / "tests/unit/nope.py").write_text("")
    assert not any("nope.py" in line for line in parity.check(INVENTORY, doc, gate, tmp_path)[1])


@pytest.mark.parametrize("gate", [False, True])
def test_stale_status_fails_in_both_modes(tmp_path: Path, gate: bool) -> None:
    doc = doc_with(FULL)
    changed = INVENTORY.replace("partial P3", "built P3")
    rc, lines = parity.check(changed, doc, gate, tmp_path)
    assert rc == 1 and any("stale: run make parity-sync" in line and "A1.2" in line for line in lines)
    rc, lines = parity.check(INVENTORY, doc.replace("| A1.4 |", "| A1.40 |"), gate, tmp_path)
    assert rc == 1 and any("stale" in line for line in lines)


def test_ledger_evidence_with_a_snippet_is_checked_by_path(tmp_path: Path) -> None:
    cite = ".superpowers/sdd/w/progress" + ".md" + ":3" + ' "Task 1"'
    doc = doc_with({**FULL, "A1.1": cite + " " + cite})
    rc, lines = parity.check(INVENTORY, doc, False, tmp_path)
    assert rc == 1 and any(".superpowers/sdd/w/progress.md" in line for line in lines)
    (tmp_path / ".superpowers/sdd/w").mkdir(parents=True)
    (tmp_path / ".superpowers/sdd/w/progress.md").write_text("a\nb\nTask 1\n")
    rc, lines = parity.check(INVENTORY, doc, False, tmp_path)
    assert rc == 0, lines


def test_missing_inventory_names_trex_plans(tmp_path: Path) -> None:
    r = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "check", "--plans", str(tmp_path)],
                       check=False, capture_output=True, text=True)
    assert r.returncode == 2 and "TREX_PLANS" in r.stderr


def test_unescaped_pipe_in_evidence_is_malformed_not_stale(tmp_path: Path) -> None:
    doc = doc_with({**FULL, "A1.1": "`tests/a.py::test_x` | `tests/b.py::test_y`"})
    rc, lines = parity.check(INVENTORY, doc, False, tmp_path)
    assert rc == 1
    assert any("A1.1" in line and "malformed" in line and "escape | as \\|" in line for line in lines)
    assert not any("A1.1" in line and "stale" in line for line in lines)


def test_sync_refuses_to_rewrite_a_malformed_row() -> None:
    doc = doc_with({**FULL, "A1.1": "`tests/a.py::test_x` | `tests/b.py::test_y`"})
    with pytest.raises(parity.ParityError, match=r"A1\.1.*escape \| as"):
        parity.sync(INVENTORY, doc)


def test_vitest_evidence_form_is_a_path_and_a_title(tmp_path: Path) -> None:
    title = "caps the log at 2000"
    form = f'`frontend/src/state.test.ts "{title}"`'
    assert parity.evidence_paths(form) == ["frontend/src/state.test.ts"]
    doc = doc_with({**FULL, "A1.1": form})
    rc, lines = parity.check(INVENTORY, doc, False, tmp_path)
    assert rc == 1 and any("frontend/src/state.test.ts" in line for line in lines)
    (tmp_path / "frontend/src").mkdir(parents=True)
    (tmp_path / "frontend/src/state.test.ts").write_text("it('other', () => {})\n")
    rc, lines = parity.check(INVENTORY, doc, False, tmp_path)
    assert rc == 1 and any("A1.1" in line and title in line for line in lines)
    (tmp_path / "frontend/src/state.test.ts").write_text(f"  it('{title} and rpc at 500', () => {{}})\n")
    assert parity.check(INVENTORY, doc, False, tmp_path)[0] == 0


def test_rc1_use_form_in_backticks_is_not_a_path() -> None:
    assert parity.evidence_paths("`rc1 use, YYYY-MM-DD`") == []
    assert parity.evidence_paths("`rc1 use, 2026-11-01`") == []
