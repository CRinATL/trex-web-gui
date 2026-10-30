"""scripts/check_docstrings.py: what counts as documented, how names are qualified, and the baseline gate."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from support import ROOT

SCRIPT = ROOT / "scripts" / "check_docstrings.py"


@pytest.fixture(scope="module")
def cd():
    """The checker module, loaded by path on first use so a missing script fails each test, not collection."""
    spec = importlib.util.spec_from_file_location("check_docstrings", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def names(cd, source: str, filename: str = "x.py") -> list[str]:
    """Qualified names the checker reports for `source` saved as `filename`."""
    return [name for _, name in cd.undocumented(Path(filename), source)]


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """The checker as a subprocess against `repo`."""
    return subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--repo", str(repo), *args],
                          check=False, capture_output=True, text=True)


def test_docstring_counts(cd):
    assert names(cd, 'def f():\n    """Does it."""\n\ndef g():\n    pass\n') == ["g"]


def test_comment_line_above_counts(cd):
    assert names(cd, "# does it\ndef f():\n    pass\n") == []


def test_comment_above_first_decorator_counts(cd):
    src = "class A:\n    # x\n    @property\n    def p(self):\n        return 1\n"
    assert names(cd, src) == []


def test_comment_between_decorator_and_def_counts(cd):
    src = "class A:\n    @property\n    # x\n    def p(self):\n        return 1\n"
    assert names(cd, src) == []


def test_comment_deeper_than_the_def_above_a_decorator_does_not_count(cd):
    # one column past the '@' is deeper than the def: the end of the previous body, not this function's
    src = "class A:\n     # x\n    @property\n    def p(self):\n        return 1\n"
    assert names(cd, src) == ["A.p"]


def test_trailing_comment_on_def_line_counts(cd):
    assert names(cd, "def f():  # does it\n    pass\n") == []


def test_trailing_comment_on_the_closing_line_of_a_multiline_signature_does_not_count(cd):
    assert names(cd, "def f(\n    a,\n) -> None:  # x\n    pass\n") == ["f"]


def test_trailing_comment_on_the_previous_statement_does_not_count(cd):
    assert names(cd, "x = 1  # foo\ndef f(): pass\n") == ["f"]


def test_section_banner_above_def_does_not_count(cd):
    assert names(cd, "# -- snapshot -----\ndef f(): pass\n\n# ===\ndef g(): pass\n") == ["f", "g"]


def test_pragma_comment_does_not_count(cd):
    assert names(cd, "# noqa: E501\ndef f(): pass\n\ndef g(): pass  # type: ignore\n") == ["f", "g"]


def test_hash_inside_a_string_default_does_not_count(cd):
    assert names(cd, 'def f(x="#"): pass\n') == ["f"]


def test_comment_ending_the_previous_body_does_not_count(cd):
    assert names(cd, "def f():\n    '''Doc.'''\n    # tail of f\ndef g(): pass\n") == ["g"]


def test_empty_comment_does_not_count(cd):
    assert names(cd, "#\ndef f(): pass\n\ndef g(): pass  #\n") == ["f", "g"]


def test_blank_line_between_comment_and_def_does_not_count(cd):
    assert names(cd, "# does it\n\ndef f(): pass\n") == ["f"]


def test_async_def_is_checked(cd):
    assert names(cd, "async def f(): pass\n\nasync def g():\n    '''Does it.'''\n") == ["f"]


def test_methods_and_nested_functions_get_qualified_names(cd):
    src = "class A:\n    def m(self):\n        def inner():\n            pass\n"
    assert names(cd, src) == ["A.m", "A.m.inner"]


def test_test_functions_and_their_nested_functions_are_skipped_in_test_files(cd):
    assert names(cd, "def test_a():\n    def helper():\n        pass\n", "test_x.py") == []


def test_helper_in_a_test_file_is_checked(cd):
    assert names(cd, "def make_thing(): pass\n", "test_x.py") == ["make_thing"]


def test_test_named_function_outside_a_test_file_is_checked(cd):
    assert names(cd, "def test_a(): pass\n", "x.py") == ["test_a"]


def test_vendor_and_pycache_are_skipped(cd, tmp_path):
    for d in ("pkg", "pkg/_vendor", "pkg/__pycache__"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
        (tmp_path / d / "m.py").write_text("def f(): pass\n")
    assert cd.scan(tmp_path, [tmp_path / "pkg"]) == [("pkg/m.py", 1, "f")]


def test_root_outside_repo_is_a_usage_error(tmp_path):
    repo, other = tmp_path / "repo", tmp_path / "other"
    repo.mkdir()
    other.mkdir()
    r = run_cli(repo, str(other))
    assert r.returncode == 2
    assert "outside the repo" in r.stderr


def test_missing_repo_is_a_usage_error(tmp_path):
    assert run_cli(tmp_path / "nope").returncode == 2


def test_missing_baseline_is_a_usage_error(tmp_path):
    (tmp_path / "m.py").write_text("def a(): pass\n")
    r = run_cli(tmp_path, "--baseline", str(tmp_path / "nope.txt"), str(tmp_path))
    assert r.returncode == 2
    assert "Traceback" not in r.stderr


def test_baseline_allows_listed_and_rejects_new(tmp_path):
    (tmp_path / "m.py").write_text("def old(): pass\n\ndef fresh(): pass\n")
    (tmp_path / "base.txt").write_text("m.py::old\n")
    r = run_cli(tmp_path, "--baseline", str(tmp_path / "base.txt"), str(tmp_path))
    assert r.returncode == 1
    assert "m.py:3: fresh has no docstring or comment" in r.stdout
    assert "old has no" not in r.stdout


def test_stale_baseline_entry_fails(tmp_path):
    (tmp_path / "m.py").write_text("def old():\n    '''Now documented.'''\n")
    (tmp_path / "base.txt").write_text("m.py::old\n")
    r = run_cli(tmp_path, "--baseline", str(tmp_path / "base.txt"), str(tmp_path))
    assert r.returncode == 1
    assert "m.py::old is documented now; remove it from the baseline" in r.stdout


def test_repeated_names_compare_as_a_multiset(tmp_path):
    (tmp_path / "m.py").write_text(
        "class A:\n    @property\n    def p(self):\n        '''Get.'''\n"
        "    @p.setter\n    def p(self, v): pass\n")
    (tmp_path / "base.txt").write_text("m.py::A.p\nm.py::A.p\n")
    r = run_cli(tmp_path, "--baseline", str(tmp_path / "base.txt"), str(tmp_path))
    assert r.returncode == 1
    assert r.stdout.count("remove it from the baseline") == 1
    assert "has no docstring" not in r.stdout


def test_summary_line_is_last(tmp_path):
    (tmp_path / "m.py").write_text("def a(): pass\n\ndef b():\n    '''Doc.'''\n")
    r = run_cli(tmp_path, str(tmp_path))
    assert r.stdout.splitlines()[-1] == "docstrings: 2 functions, 1 undocumented, 0 in baseline"
    w = run_cli(tmp_path, "--write-baseline", str(tmp_path / "out.txt"), str(tmp_path))
    assert w.returncode == 0
    assert w.stdout.splitlines()[-1] == "docstrings: 2 functions, 1 undocumented, 1 in baseline"
    assert (tmp_path / "out.txt").read_text() == "m.py::a\n"


def test_gate_fails_on_an_undocumented_function_without_a_baseline(tmp_path):
    (tmp_path / "m.py").write_text("def _probe(): pass\n")
    r = run_cli(tmp_path, str(tmp_path))
    assert r.returncode == 1
    assert "_probe" in r.stdout
