"""The fake-honesty register: what a test fake assumes without a recording is listed in
tests/fake/unrecorded.py, and this check fails on an assumption that is not.

The scanner functions are tested first on crafted source strings (a negative control for each rule), then the
repository checks run them over every fake in FAKES."""

import ast
import json
import re
from functools import cache
from pathlib import Path

import pytest
from fake.unrecorded import FAKE_NAMES, REGISTER, Entry

ROOT = Path(__file__).resolve().parent.parent.parent
FAKES: dict[str, Path] = {
    "fake_emu": ROOT / "tests" / "fake" / "fake_emu.py",
    "fake_trex": ROOT / "tests" / "fake" / "fake_trex.py",
    "fake_daemon": ROOT / "tests" / "fake" / "fake_daemon.py",
}

TAG = re.compile(r"\[U:((?:emu|trex|daemon)-[a-z0-9-]+)\]")
MARKER = re.compile(r"(?i)\b(made[- ]up|not recorded|invented|assumed|unverified)\b")
HEADER = "Known divergences from the real server:"
UPPER = re.compile(r"[A-Z][A-Z0-9_]*")
PLACEHOLDER = re.compile(r"\{[^{}]*\}")    # a skeleton's f-string field
ID = re.compile(r"^(emu|trex|daemon)-[a-z0-9-]+$")
KINDS = {"text", "behaviour", "model", "hook"}
SETTLE_REAL = re.compile(r"^real:(tests/real/[\w]+\.py)::(test_\w+)$")
HOW = "register it in tests/fake/unrecorded.py and tag it [U:<id>] (the rule is in that module's docstring)"


def test_fakes_dict_lists_the_register_names() -> None:
    """The fakes this module scans are the register's FAKE_NAMES, in order: a fourth fake added to one list
    only would be scanned without a register, or registered without a scan (daemon-register-pr-b-13)."""
    assert tuple(FAKES) == FAKE_NAMES


def tags(text: str) -> list[str]:
    """The ids of the `[U:<id>]` tags in `text`, in order."""
    return TAG.findall(text)


def _docstring_node(tree: ast.Module) -> ast.Constant | None:
    """The module docstring's constant node, or None."""
    first = tree.body[0] if tree.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first.value
    return None


def _section(src: str) -> tuple[int, list[str]] | None:
    """(line number of the docstring's first line, the docstring's raw lines), or None for a module without a
    docstring. The lines are cut from the source between the quotes, not from the string's value, so an escape
    such as `\\n` cannot shift them off their line numbers."""
    node = _docstring_node(ast.parse(src))
    if node is None:
        return None
    seg = ast.get_source_segment(src, node)
    opening = re.match(r"(?i)[rbu]*(\"\"\"|'''|\"|')", seg or "")
    assert seg is not None and opening is not None and seg.endswith(opening.group(1)), "one string literal"
    return node.lineno, seg[opening.end():len(seg) - len(opening.group(1))].split("\n")


def _section_span(lines: list[str]) -> tuple[int, int] | None:
    """(index of the header line, index one past the section's last line) in the docstring's lines."""
    start = next((i for i, ln in enumerate(lines) if ln == HEADER), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end].startswith(("- ", "  "))):
        end += 1
    return start, end


def divergence_items(src: str) -> list[tuple[int, str]]:
    """(first line, text) of each `- ` item of the "Known divergences" section in the module docstring."""
    doc = _section(src)
    if doc is None:
        return []
    first, lines = doc
    span = _section_span(lines)
    if span is None:
        return []
    items: list[tuple[int, list[str]]] = []
    for i in range(span[0] + 1, span[1]):
        if lines[i].startswith("- "):
            items.append((first + i, [lines[i][2:].strip()]))
        elif items and lines[i].strip():
            items[-1][1].append(lines[i].strip())
    return [(n, " ".join(parts)) for n, parts in items]


def untagged_markers(src: str) -> list[int]:
    """The lines outside the "Known divergences" section that match MARKER and carry no tag."""
    doc = _section(src)
    skipped: set[int] = set()
    if doc is not None and (span := _section_span(doc[1])) is not None:
        skipped = set(range(doc[0] + span[0], doc[0] + span[1]))
    return [
        n
        for n, ln in enumerate(src.split("\n"), 1)
        if n not in skipped and MARKER.search(ln) and not TAG.search(ln)
    ]


def _skeleton(node: ast.expr) -> str | None:
    """The constant text of a str constant or f-string with each field written `{}`, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(p.value if isinstance(p, ast.Constant) else "{}" for p in node.values)
    return None


def reply_literals(src: str) -> list[tuple[int, str]]:
    """(line, skeleton) of each reply literal: the str value of a "message" or "specific_err" key in a dict
    display, the first argument of `ValueError(...)`, and a module-level UPPER_CASE str constant
    (plain or annotated assignment)."""
    tree = ast.parse(src)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value in ("message", "specific_err")
                    and (sk := _skeleton(value)) is not None
                ):
                    out.append((value.lineno, sk))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ValueError"
            and node.args
            and (sk := _skeleton(node.args[0])) is not None
        ):
            out.append((node.args[0].lineno, sk))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        if (
            isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and any(isinstance(t, ast.Name) and UPPER.fullmatch(t.id) for t in targets)
        ):
            out.append((node.value.lineno, node.value.value))
    return sorted(out)


def recorded(skeleton: str, values: list[str]) -> bool:
    """True when the skeleton (placeholders read as `.+?`) fullmatches a whole string in `values`. A skeleton
    with no literal character outside its placeholders is never recorded: it would match any string."""
    if not PLACEHOLDER.sub("", skeleton):
        return False
    pattern = ".+?".join(re.escape(part) for part in PLACEHOLDER.split(skeleton))
    rx = re.compile(pattern, re.DOTALL)
    return any(rx.fullmatch(v) for v in values)


def _strings(node: object) -> list[str]:
    """Every string value (not key) in a parsed JSON document."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    return []


@cache
def _fixture_strings() -> tuple[str, ...]:
    """Every string value in tests/fixtures/**/*.json and *.jsonl (each jsonl line parsed on its own)."""
    out: list[str] = []
    for path in sorted((ROOT / "tests" / "fixtures").rglob("*")):
        if path.suffix == ".json":
            out += _strings(json.loads(path.read_text()))
        elif path.suffix == ".jsonl":
            for line in path.read_text().splitlines():
                if line.strip():
                    out += _strings(json.loads(line))
    return tuple(out)


def fixture_strings() -> list[str]:
    """The strings the recorded fixtures hold."""
    return list(_fixture_strings())


def missing_settlements(register: dict[str, Entry], root: Path) -> list[str]:
    """One message per `real:` settle whose file has no top-level `def` of the named test (read by AST, never
    imported)."""
    out: list[str] = []
    for key, entry in register.items():
        m = SETTLE_REAL.match(entry.settle)
        if not m:
            continue
        path = root / m.group(1)
        names: set[str] = set()
        if path.is_file():
            names = {
                n.name
                for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            }
        if m.group(2) not in names:
            out.append(f"{key}: {entry.settle} names no test that exists")
    return out


def unknown_tags(src: str, register: dict[str, Entry]) -> list[tuple[int, str]]:
    """(line, id) of each tag in `src` that names no entry of `register`."""
    return [(n, t) for n, ln in enumerate(src.split("\n"), 1) for t in tags(ln) if t not in register]


def untagged_entries(src: str, register: dict[str, Entry]) -> list[str]:
    """The ids of the entries of `register` that no tag in `src` names."""
    return sorted(set(register) - set(tags(src)))


def unrecordable_literals(src: str) -> list[tuple[int, str]]:
    """(line, skeleton) of each reply literal with no literal character outside its placeholders."""
    return [(n, sk) for n, sk in reply_literals(src) if not PLACEHOLDER.sub("", sk)]


def unregistered_literals(src: str, register: dict[str, Entry], values: list[str]) -> list[tuple[int, str]]:
    """(line, skeleton) of each reply literal that is neither recorded nor the `text` of an entry."""
    texts = {e.text for e in register.values() if e.text is not None}
    return [(n, sk) for n, sk in reply_literals(src) if not recorded(sk, values) and sk not in texts]


def unmatched_texts(src: str, register: dict[str, Entry]) -> list[str]:
    """The ids of the entries whose `text` equals the skeleton of no reply literal in `src`."""
    skeletons = {sk for _, sk in reply_literals(src)}
    return [k for k, e in register.items() if e.text is not None and e.text not in skeletons]


def recorded_texts(src: str, register: dict[str, Entry], values: list[str]) -> list[str]:
    """The ids of the entries whose `text` matches a literal of `src` and is now recorded."""
    skeletons = {sk for _, sk in reply_literals(src)}
    return [k for k, e in register.items() if e.text in skeletons and recorded(e.text, values)]


# negative controls: the scanners, on crafted sources


def test_scanner_flags_an_untagged_marker() -> None:
    """A marker line with no tag is reported by line; a tagged one is not."""
    src = '"""doc"""\nX = 1  # made up\nY = 2  # made up [U:emu-x]\n'
    assert untagged_markers(src) == [2]


def test_scanner_flags_an_untagged_divergence_item() -> None:
    """Each item of the section is read with its tags, so an untagged one shows as an empty list."""
    src = (
        '"""Intro.\n\n' + HEADER + "\n- first thing.\n- second thing,\n  continued. [U:emu-a]\n\n"
        'Next paragraph.\n"""\n'
    )
    assert [tags(t) for _, t in divergence_items(src)] == [[], ["emu-a"]]
    assert [n for n, _ in divergence_items(src)] == [4, 5]


def test_scanner_reads_docstring_lines_from_the_source() -> None:
    """A `\\n` escape adds a line to the docstring's value but not to the source: an item keeps its source
    line, and a marker on the line after the docstring is still outside the section."""
    src = '"""Intro \\n escape.\n\n' + HEADER + '\n- item. [U:emu-a]\n"""\nX = 1  # made up\n'
    assert [n for n, _ in divergence_items(src)] == [4]
    assert untagged_markers(src) == [6]


def test_scanner_finds_the_three_literal_forms() -> None:
    """A message value, a ValueError argument (f-string fields as `{}`) and an UPPER_CASE constant."""
    src = 'E = {"message": " x "}\nraise_it = ValueError(f"n={n} max")\nTEXT = "y"\nlower = "z"\n'
    assert [s for _, s in reply_literals(src)] == [" x ", "n={} max", "y"]


def test_recorded_matches_a_placeholder_skeleton() -> None:
    """A `{plugin}` placeholder stands for the recorded plugin name."""
    assert recorded(
        "Error there is no valid plugin {plugin} for this client ",
        ["Error there is no valid plugin dhcp for this client "],
    )


def test_recorded_needs_a_whole_recorded_string() -> None:
    """A skeleton inside a longer recorded string is not recorded: the match is a fullmatch."""
    assert not recorded(" error", [" error there is no valid plugin igmp for this tunnel "])


def test_missing_settlement_is_reported() -> None:
    """A `real:` settle naming a test the file lacks is listed; one naming a test that exists is not."""
    gone = Entry("fake_emu", "behaviour", "x", "real:tests/real/test_emu_real.py::test_does_not_exist")
    there = Entry(
        "fake_emu",
        "behaviour",
        "x",
        "real:tests/real/test_emu_real.py::test_the_server_ignores_mask_on_a_meta_counter_request",
    )
    missing = missing_settlements({"emu-gone": gone}, ROOT)
    assert len(missing) == 1
    assert "test_does_not_exist" in missing[0]
    assert missing_settlements({"emu-there": there}, ROOT) == []


def test_scanner_finds_an_annotated_constant() -> None:
    """A module-level `NAME: str = "..."` is a reply literal like the plain assignment."""
    assert [s for _, s in reply_literals('TEXT: str = " z "\nlower: str = "q"\nBARE: str\n')] == [" z "]


def test_a_skeleton_of_only_placeholders_is_never_recorded() -> None:
    """`{}` would read as `.+?` and match any string, so it is refused and reported as unrecordable."""
    assert not recorded("{}", ["anything at all"])
    assert not recorded("{a}{b}", ["anything at all"])
    assert unrecordable_literals('ValueError(f"{x}")\nTEXT = " ok "\n') == [(1, "{}")]


def test_an_unknown_tag_is_reported_with_its_line() -> None:
    """A tag naming no entry shows with its line; one naming an entry does not."""
    reg = {"emu-a": Entry("fake_emu", "behaviour", "x", "open: y")}
    src = "X = 1  # [U:emu-a]\nY = 2  # [U:emu-bogus]\n"
    assert unknown_tags(src, reg) == [(2, "emu-bogus")]


def test_an_entry_tagged_nowhere_is_reported() -> None:
    """An entry no tag names is listed; a tagged one is not."""
    reg = {
        "emu-a": Entry("fake_emu", "behaviour", "x", "open: y"),
        "emu-b": Entry("fake_emu", "model", "x", "open: y"),
    }
    assert untagged_entries("X = 1  # [U:emu-a]\n", reg) == ["emu-b"]


def test_an_entry_text_matching_no_literal_is_reported() -> None:
    """An entry whose text is the skeleton of no literal is listed; one whose text matches is not."""
    src = 'E = {"message": " x "}\n'
    reg = {
        "emu-a": Entry("fake_emu", "text", "x", "open: y", text=" x "),
        "emu-b": Entry("fake_emu", "text", "x", "open: y", text=" nope "),
    }
    assert unmatched_texts(src, reg) == ["emu-b"]


def test_an_entry_text_now_recorded_is_reported() -> None:
    """An entry whose text a fixture string now holds is listed; an unrecorded one is not."""
    src = 'E = {"message": " x "}\nF = {"message": " y "}\n'
    reg = {
        "emu-a": Entry("fake_emu", "text", "x", "open: y", text=" x "),
        "emu-b": Entry("fake_emu", "text", "x", "open: y", text=" y "),
    }
    assert recorded_texts(src, reg, [" x "]) == ["emu-a"]


def test_an_unregistered_literal_is_reported() -> None:
    """A literal neither recorded nor an entry's text is listed."""
    src = 'E = {"message": " x "}\nF = {"message": " y "}\nG = {"message": " z "}\n'
    reg = {"emu-a": Entry("fake_emu", "text", "x", "open: y", text=" x ")}
    assert unregistered_literals(src, reg, [" y "]) == [(3, " z ")]


# the repository checks


@pytest.mark.parametrize("fake", FAKES)
def test_every_marker_line_carries_a_tag(fake: str) -> None:
    """A line saying made up, not recorded, invented, assumed or unverified carries a [U:<id>] tag."""
    bad = untagged_markers(FAKES[fake].read_text())
    assert not bad, f"{FAKES[fake].name} lines {bad} carry an untagged marker: {HOW}"


@pytest.mark.parametrize("fake", FAKES)
def test_every_divergence_item_carries_a_tag(fake: str) -> None:
    """The "Known divergences" section exists, lists at least one item, and tags every item."""
    items = divergence_items(FAKES[fake].read_text())
    assert items, f"{FAKES[fake].name} has no '{HEADER}' section with items in its docstring"
    bad = [n for n, text in items if not tags(text)]
    assert not bad, f"{FAKES[fake].name} divergence items at lines {bad} carry no tag: {HOW}"


def _mine(fake: str) -> dict[str, Entry]:
    """The entries of REGISTER that belong to `fake`."""
    return {k: e for k, e in REGISTER.items() if e.fake == fake}


def literal_problems(src: str, name: str, reg: dict[str, Entry], values: list[str]) -> list[str]:
    """One message per reply literal of `src` with no constant text or neither recorded nor registered, and
    per entry of `reg` whose text matches no literal or is now recorded."""
    problems = [
        f"line {n}: {sk!r} has no constant text, so it cannot be recorded"
        for n, sk in unrecordable_literals(src)
    ]
    problems += [
        f"line {n}: {sk!r} is neither recorded nor registered: {HOW}"
        for n, sk in unregistered_literals(src, reg, values)
    ]
    problems += [
        f"{k}: text {reg[k].text!r} matches no reply literal of {name}" for k in unmatched_texts(src, reg)
    ]
    problems += [
        f"{k}: text {reg[k].text!r} is now recorded; remove the entry and its tags"
        for k in recorded_texts(src, reg, values)
    ]
    return problems


@pytest.mark.parametrize("fake", FAKES)
def test_every_reply_literal_is_recorded_or_registered(fake: str) -> None:
    """A reply literal is recorded or covered by an entry's text, and has constant text; an entry's text
    matches a literal and is not recorded."""
    problems = literal_problems(FAKES[fake].read_text(), FAKES[fake].name, _mine(fake), fixture_strings())
    assert not problems, "\n".join(problems)


def test_literal_problems_reports_each_kind() -> None:
    """Control for the check's wiring: each of its four problems shows once."""
    src = 'A = ValueError(f"{x}")\nE = {"message": " y "}\nG = {"message": " z "}\n'
    reg = {
        "emu-a": Entry("fake_emu", "text", "x", "open: y", text=" nope "),
        "emu-b": Entry("fake_emu", "text", "x", "open: y", text=" y "),
    }
    assert literal_problems(src, "f.py", reg, [" y "]) == [
        "line 1: '{}' has no constant text, so it cannot be recorded",
        f"line 1: '{{}}' is neither recorded nor registered: {HOW}",
        f"line 3: ' z ' is neither recorded nor registered: {HOW}",
        "emu-a: text ' nope ' matches no reply literal of f.py",
        "emu-b: text ' y ' is now recorded; remove the entry and its tags",
    ]


def tag_problems(src: str, name: str, fake: str, reg: dict[str, Entry]) -> list[str]:
    """One message for the tags of `src` that name no entry of `reg`, and one for the entries no tag names."""
    problems: list[str] = []
    if unknown := unknown_tags(src, reg):
        problems.append(f"{name} tags (line, id) {unknown} name no entry of {fake}: {HOW}")
    if untagged := untagged_entries(src, reg):
        problems.append(f"entries {untagged} of {fake} are tagged nowhere in {name}")
    return problems


@pytest.mark.parametrize("fake", FAKES)
def test_tags_and_entries_match(fake: str) -> None:
    """Every tag in the source names an entry of this fake, and every entry of this fake is tagged."""
    problems = tag_problems(FAKES[fake].read_text(), FAKES[fake].name, fake, _mine(fake))
    assert not problems, "\n".join(problems)


def test_tag_problems_reports_each_kind() -> None:
    """Control for the check's wiring: an unknown tag and an untagged entry each give their message."""
    reg = {"emu-a": Entry("fake_emu", "behaviour", "x", "open: y")}
    assert tag_problems("X = 1  # [U:emu-bogus]\n", "f.py", "fake_emu", reg) == [
        f"f.py tags (line, id) [(1, 'emu-bogus')] name no entry of fake_emu: {HOW}",
        "entries ['emu-a'] of fake_emu are tagged nowhere in f.py",
    ]


def register_problems(register: dict[str, Entry], root: Path) -> list[str]:
    """One message per malformed id, fake, kind, `assumes`, `text` or settle of an entry of `register`, and
    per `real:` settle naming a test that does not exist under `root`."""
    problems: list[str] = []
    for key, e in register.items():
        if not ID.match(key):
            problems.append(f"{key}: id does not match {ID.pattern}")
        elif key.split("-", 1)[0] != e.fake.removeprefix("fake_"):
            problems.append(f"{key}: id prefix does not match fake {e.fake!r}")
        if e.fake not in FAKES:
            problems.append(f"{key}: fake {e.fake!r} is none of {', '.join(FAKES)}")
        if e.kind not in KINDS:
            problems.append(f"{key}: kind {e.kind!r} is not one of {sorted(KINDS)}")
        if not e.assumes.strip():
            problems.append(f"{key}: assumes is empty")
        if e.kind == "text" and e.text is None:
            problems.append(f"{key}: a text entry needs text")
        if e.kind in ("behaviour", "model") and e.text is not None:
            problems.append(f"{key}: a {e.kind} entry carries no text")
        if not (SETTLE_REAL.match(e.settle) or re.match(r"^(open|hook): \S", e.settle)):
            problems.append(f"{key}: settle {e.settle!r} is not real:<test>, open: <why> or hook: <flag>")
        if e.kind == "hook" and not e.settle.startswith("hook: "):
            problems.append(f"{key}: a hook entry settles with 'hook: '")
    problems += missing_settlements(register, root)
    return problems


def test_every_settlement_is_well_formed_and_exists() -> None:
    """Ids, kinds, `text` by kind and the three settle forms are well formed, and each `real:` test exists."""
    problems = register_problems(REGISTER, ROOT)
    assert not problems, "\n".join(problems)


def test_register_problems_reports_each_rule() -> None:
    """Control for the check's wiring: one entry breaking each rule gives that rule's message, and a
    well-formed entry gives none."""
    gone = "real:tests/real/test_emu_real.py::test_does_not_exist"
    bad = {
        "Bad_id": Entry("fake_emu", "behaviour", "x", "open: y"),
        "trex-a": Entry("fake_emu", "behaviour", "x", "open: y"),
        "emu-b": Entry("fake_other", "behaviour", "x", "open: y"),
        "emu-c": Entry("fake_emu", "guess", "x", "open: y"),
        "emu-d": Entry("fake_emu", "behaviour", " ", "open: y"),
        "emu-e": Entry("fake_emu", "text", "x", "open: y"),
        "emu-f": Entry("fake_emu", "model", "x", "open: y", text=" t "),
        "emu-g": Entry("fake_emu", "behaviour", "x", "later"),
        "emu-h": Entry("fake_emu", "hook", "x", "open: y"),
        "emu-i": Entry("fake_emu", "behaviour", "x", gone),
        "emu-ok": Entry("fake_emu", "hook", "x", "hook: --flag"),
        "daemon-ok": Entry("fake_daemon", "behaviour", "x", "open: y"),
    }
    assert register_problems(bad, ROOT) == [
        f"Bad_id: id does not match {ID.pattern}",
        "trex-a: id prefix does not match fake 'fake_emu'",
        "emu-b: id prefix does not match fake 'fake_other'",
        "emu-b: fake 'fake_other' is none of fake_emu, fake_trex, fake_daemon",
        f"emu-c: kind 'guess' is not one of {sorted(KINDS)}",
        "emu-d: assumes is empty",
        "emu-e: a text entry needs text",
        "emu-f: a model entry carries no text",
        "emu-g: settle 'later' is not real:<test>, open: <why> or hook: <flag>",
        "emu-h: a hook entry settles with 'hook: '",
        f"emu-i: {gone} names no test that exists",
    ]


SUMMARY = ROOT / "tests" / "fixtures" / "p7" / "summary.json"


def unregistered_crashes(summary: dict, register: dict[str, Entry]) -> list[str]:
    """The ids `emu-crash-<step>` that `register` lacks, one per E3 step of `summary` recorded unanswered."""
    want = (
        "emu-crash-" + st["name"].removeprefix("e3_").replace("_", "-")
        for st in summary["e3"]["steps"]
        if "unanswered" in st["outcome"]
    )
    return [i for i in want if i not in register]


def test_every_unanswered_e3_step_has_its_crash_entry() -> None:
    """A re-record that leaves a step unanswered makes the fake fall back for it; the register must say so."""
    problems = unregistered_crashes(json.loads(SUMMARY.read_text()), REGISTER)
    assert not problems, f"register an entry for each recorded crash: {problems}"


def test_an_unanswered_step_without_an_entry_is_one_problem() -> None:
    """Control: one unanswered step and no entry gives one problem; with the entry it gives none."""
    summary = {"e3": {"steps": [{"name": "e3_ok", "outcome": {"result": True}},
                                {"name": "e3_ctx_x_y", "outcome": "unanswered"}]}}
    assert unregistered_crashes(summary, {}) == ["emu-crash-ctx-x-y"]
    have = {"emu-crash-ctx-x-y": Entry("fake_emu", "behaviour", "x", "open: y")}
    assert unregistered_crashes(summary, have) == []
