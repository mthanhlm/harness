"""A check that did not run must never be reported as a check that passed.

Four defects lived here, all of the same shape: the code produced an answer it
did not have, and every caller believed it. None of them raised, and none were
covered by a test — the suite passed unchanged before and after the fix, which
is how they survived.

1. A check that timed out returned `ok=True`, so the end-of-turn gate told the
   user "checks that did pass: pytest" for a suite that never finished.
2. A missing binary did the same.
3. A *baseline* run that timed out returned an empty diagnostic set, which is
   indistinguishable from a baseline that ran and found nothing — so every
   pre-existing problem in the file looked new and the edit was blocked for
   breakage it did not cause. That is the exact failure this module exists to
   prevent, and a slow type-checker was enough to cause it.
4. Comparison stripped every number from a diagnostic, so `Expected 2 arguments,
   but got 1` and `Expected 5 arguments, but got 3` were the same string. A
   genuinely new type error was silently swallowed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import runner

SLEEP = {"kind": "test", "label": "slow suite", "argv": ["sleep", "5"], "blocking": True}
MISSING = {"kind": "lint", "label": "ghosttool", "argv": ["harness-no-such-binary"], "blocking": True}


def test_a_timed_out_check_is_not_reported_as_a_pass(tmp_path):
    result = runner.run_project_check(SLEEP, tmp_path, timeout=1)

    assert result.skipped, "a suite that never finished was indistinguishable from one that passed"
    assert "timed out" in result.skipped
    assert not result.blocking, "a slow tool must still never block"


def test_a_missing_binary_is_not_reported_as_a_pass(tmp_path):
    result = runner.run_project_check(MISSING, tmp_path)

    assert result.skipped, "an uninstalled tool was counted as a passing check"
    assert not result.blocking, "a missing tool is not grounds to block"


def test_a_check_that_genuinely_passes_is_not_marked_skipped(tmp_path):
    """The other direction, so the two tests above cannot pass by marking
    everything as skipped — which would empty the gate entirely."""
    ok = {"kind": "lint", "label": "true", "argv": ["true"], "blocking": True}

    result = runner.run_project_check(ok, tmp_path)

    assert result.ok and result.skipped is None


def test_a_check_that_genuinely_fails_still_blocks(tmp_path):
    """The most important direction of all."""
    bad = {"kind": "lint", "label": "false", "argv": ["false"], "blocking": True}

    result = runner.run_project_check(bad, tmp_path)

    assert result.blocking and result.skipped is None


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    for argv in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                 ["git", "config", "user.name", "t"], ["git", "add", "-A"],
                 ["git", "commit", "-qm", "base"]):
        subprocess.run(argv, cwd=str(root), check=True, capture_output=True)
    return root


def test_an_unrunnable_baseline_does_not_blame_the_edit(tmp_path, monkeypatch):
    """The worst of the four. If the baseline cannot be established, nothing in
    the file is attributable to the edit — so the honest answer is "unknown",
    and the safe one is to let it through. Reporting the inherited problems as
    new is what got this plugin's predecessor uninstalled.
    """
    root = _repo(tmp_path)
    target = root / "a.py"
    target.write_text("x = 2\n", encoding="utf-8")

    calls = {"n": 0}
    real = runner._run

    def flaky(argv, cwd, timeout):
        # First call is the check on the real file: let it fail normally.
        # Second is the baseline: make it time out.
        calls["n"] += 1
        if calls["n"] == 1:
            return False, "a.py:1:1: E999 pre-existing problem", None
        return True, "", f"timed out after {timeout}s"

    monkeypatch.setattr(runner, "_run", flaky)
    check = {"kind": "lint", "label": "ruff", "argv": ["ruff", "{file}"], "blocking": True}

    result = runner.run_file_check(check, root, target)

    assert calls["n"] == 2, "the baseline was never attempted"
    assert not result.blocking, "an inherited problem was blamed on the edit"
    assert result.skipped and "no baseline" in result.skipped
    monkeypatch.setattr(runner, "_run", real)


def test_a_new_file_still_reports_everything_in_it(tmp_path, monkeypatch):
    """`no baseline because the run failed` and `no baseline because the file is
    new` are different answers, and collapsing them the other way would empty
    the per-edit gate for every newly created file."""
    root = _repo(tmp_path)
    target = root / "brand_new.py"
    target.write_text("import os\n", encoding="utf-8")

    monkeypatch.setattr(runner, "_run", lambda *a, **k: (False, "brand_new.py:1:1: F401 unused", None))
    check = {"kind": "lint", "label": "ruff", "argv": ["ruff", "{file}"], "blocking": True}

    result = runner.run_file_check(check, root, target)

    assert result.blocking, "a new file's own problems were not attributed to it"
    assert result.new_diagnostics


# --- diagnostic comparison ----------------------------------------------------

@pytest.mark.parametrize("head,new,should_differ,why", [
    ("app.ts(40,5): error TS2554: Expected 2 arguments, but got 1.",
     "app.ts(88,9): error TS2554: Expected 5 arguments, but got 3.",
     True, "a different arity is a different bug and was being swallowed"),
    ("app.ts(40,5): error TS2554: Expected 2 arguments, but got 1.",
     "app.ts(97,5): error TS2554: Expected 2 arguments, but got 1.",
     False, "the same error moved down the file; blocking here is the old failure"),
    ('File "main.py", line 12, in handler',
     'File "main.py", line 88, in handler',
     False, "a traceback line shifts with any edit above it"),
    ("app.ts(4,1): error TS2554: bad arity",
     "app.ts(4,1): error TS7006: implicitly has an any type",
     True, "different rule codes are different problems"),
    ("main.py:3:1: F401 unused import",
     "main.py:99:1: F401 unused import",
     False, "same rule, shifted position"),
    ("main.py:3:1: F401 unused import",
     "main.py:3:1: E501 line too long",
     True, "different rule at the same position"),
])
def test_diagnostics_are_compared_on_meaning_not_on_position(head, new, should_differ, why):
    names = ("app.ts", "main.py")
    differs = bool(set(runner._normalize(new, names)) - set(runner._normalize(head, names)))

    assert differs is should_differ, why
