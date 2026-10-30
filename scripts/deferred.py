"""Keep DEFERRED.md in step with the SDD ledgers (.superpowers/sdd/*/progress.md).

  deferred.py check   exit 1 if a ledger entry still open is missing from DEFERRED.md, or a row's text no
                      longer matches its ledger line (run at every final gate)
  deferred.py sync    append the missing entries as `open (untriaged)`; existing rows and statuses are kept
  deferred.py mark ID... --status TEXT
                      set those rows' status column (e.g. --status "fixed in 5aa0724"); exit 1, nothing
                      written, if an ID has no row

Which entries are open comes from the SDD ledger tool (`sdd-ledger.sh <ws> open`, from the
sdd-on-this-machine skill; override with SDD_LEDGER), so a `Resolved:` line closes an entry here exactly as
it does there. A row's ID is `<phase>-<ledger line>`."""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SDD = ROOT / ".superpowers" / "sdd"
LEDGER_TOOL = Path(os.environ.get(
    "SDD_LEDGER", Path.home() / ".claude/skills/sdd-on-this-machine/scripts/sdd-ledger.sh"))
PHASES = {"2026-10-16-p1-skeleton-connection": "P1", "2026-10-16-p2-live-control": "P2",
          "2026-10-17-p2-5-hardening": "P2.5", "2026-10-17-p3-min-dashboard": "P3-min",
          "2026-10-17-p3a-dashboard-port-names": "P3a", "2026-10-17-p3b-port-config": "P3b"}
ROW = re.compile(r"^\| (?P<id>[^ |]+-\d+) \| \[[^]]*\]\((?P<ledger>[^)]*)\) \| "
                 r"(?P<text>.*) \| (?P<status>[^|]*) \|$")


def phase_of(ws: Path) -> str:
    """The short phase label for a ledger workspace: a known one from PHASES, else its name minus the date."""
    # later phases: the slug after the date, e.g. 2026-10-19-p3c-capture -> p3c-capture
    return PHASES.get(ws.name, re.sub(r"^\d{4}-\d{2}-\d{2}-", "", ws.name))


def open_entries() -> list[tuple[str, int, str, Path]]:
    """(phase, ledger line, text, ledger) per open entry, by running the ledger tool on each workspace."""
    found = []
    for led in sorted(SDD.glob("*/progress.md")):
        out = subprocess.run(["bash", str(LEDGER_TOOL), str(led.parent), "open"], check=True,
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            m = re.match(r"line (\d+):(.*)", line)
            if m:
                found.append((phase_of(led.parent), int(m.group(1)), m.group(2).strip(), led))
    return found


def esc(text: str) -> str:
    """The text with `|` escaped, so it fits in a markdown table cell."""
    return text.replace("|", "\\|")


def mark(path: Path, ids: list[str], status: str) -> int:
    """Set the status column of the rows named by ids; 1, nothing written, on an unknown ID or a bad status.

    A status is bad when empty or when it contains '|'."""
    lines = path.read_text().splitlines()
    at = {m["id"]: i for i, m in enumerate(map(ROW.match, lines)) if m}
    if "|" in status or not status.strip():
        print("deferred: a status must be non-empty and contain no '|'", file=sys.stderr)
        return 1
    unknown = [i for i in ids if i not in at]
    if unknown:
        print(f"deferred: no row for {', '.join(unknown)}; nothing written", file=sys.stderr)
        return 1
    for rid in ids:
        m = ROW.match(lines[at[rid]])
        assert m is not None
        lines[at[rid]] = lines[at[rid]][:m.start("status")] + f"{status} |"
    path.write_text("\n".join(lines) + "\n")
    print(f"deferred: marked {len(ids)} row{'' if len(ids) == 1 else 's'} '{status}'")
    return 0


def main() -> int:
    """CLI: mark rows, or check or sync DEFERRED.md against the ledgers; 1 on a problem, 2 with no register
    or no ledger tool."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["check", "sync", "mark"])
    ap.add_argument("ids", nargs="*", help="mark: the row IDs, e.g. p3c-capture-140")
    ap.add_argument("--status", help="mark: the new status text")
    ap.add_argument("--file", type=Path, default=ROOT / "DEFERRED.md")
    a = ap.parse_args()
    if a.mode == "mark":
        if not a.ids or not a.status:
            ap.error("mark needs at least one ID and --status")
        return mark(a.file, a.ids, a.status)
    if a.ids or a.status:
        ap.error(f"{a.mode} takes no IDs or --status")
    if not a.file.is_file():          # checked before the tool: a public clone lacks both, and this is why
        print(f"deferred: {a.file.name} not found (records are in the private repo)", file=sys.stderr)
        return 2
    if not LEDGER_TOOL.exists():
        print(f"deferred: no ledger tool at {LEDGER_TOOL} (set SDD_LEDGER)", file=sys.stderr)
        return 2
    lines = a.file.read_text().splitlines()
    rows = {m["id"]: m for m in map(ROW.match, lines) if m}
    missing, drifted = [], []
    for phase, n, text, led in open_entries():
        row = rows.get(f"{phase}-{n}")
        if row is None:
            missing.append((phase, n, text, led))
        elif not row["text"].startswith(esc(text)[:60]):
            drifted.append(f"{phase}-{n}")
    for rid in drifted:
        print(f"deferred: {rid}: the row's text no longer matches its ledger line; fix the row by hand")
    if a.mode == "sync":
        for phase, n, text, led in missing:
            lines.append(f"| {phase}-{n} | [{phase} ledger :{n}]({led.relative_to(ROOT)}) | {esc(text)} "
                         f"| open (untriaged) |")
        a.file.write_text("\n".join(lines) + "\n")
        print(f"deferred: added {len(missing)} entr{'y' if len(missing) == 1 else 'ies'}")
        return 1 if drifted else 0
    for phase, n, text, _ in missing:
        print(f"deferred: missing {phase}-{n}: {text[:100]}")
    print(f"deferred: {len(rows)} rows, {len(missing)} missing, {len(drifted)} drifted")
    return 1 if missing or drifted else 0


if __name__ == "__main__":
    sys.exit(main())
