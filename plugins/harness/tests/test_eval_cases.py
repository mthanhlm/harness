"""Does every eval case's grader actually discriminate, before a run spends a cent?

`pytest.ini` at the repo root sets `testpaths = plugins/harness/tests` precisely
so that a bare `pytest` never walks into `evals/cases/` — those fixtures are
deliberately broken, mid-task copies of small projects. The consequence is that
nothing has ever collected or run an eval case's own tests, `slugify` included.
A grader that happens to pass on the untouched fixture is indistinguishable,
from the outside, from one that actually measures whether the work got done —
`ab.py` would report every arm as passing and nobody would notice. This file is
the check that closes that gap: for every case, the visible tests must pass on
the pristine fixture (or the case is unusable before a model ever sees it), and
the hidden grader must FAIL on that same pristine fixture (or it cannot tell a
model that did nothing from one that finished the task).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CASES = Path(__file__).resolve().parent.parent / "evals" / "cases"
CASE_DIRS = sorted(p for p in CASES.iterdir() if (p / "task.md").is_file()) if CASES.is_dir() else []

_IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache")


def _copy_fixture(case: Path, tmp_path: Path) -> Path:
    """A fresh copy of the fixture, without the caches committed alongside it."""
    repo = tmp_path / "repo"
    shutil.copytree(case / "fixture", repo, ignore=_IGNORE)
    return repo


def _run_pytest(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *argv],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("case", CASE_DIRS, ids=lambda p: p.name)
def test_a_cases_visible_tests_pass_on_the_pristine_fixture(case, tmp_path):
    """The tests the model will see must not already be red before it starts.

    If these fail on an untouched checkout, the model inherits a failure that
    is not its own, and both arms of the A/B run are penalised for nothing.
    """
    repo = _copy_fixture(case, tmp_path)
    result = _run_pytest(["tests"], repo)
    assert result.returncode == 0, (
        f"{case.name}: visible tests fail on the untouched fixture\n"
        f"{result.stdout}{result.stderr}"
    )


@pytest.mark.parametrize("case", CASE_DIRS, ids=lambda p: p.name)
def test_a_cases_hidden_grader_fails_on_the_pristine_fixture(case, tmp_path):
    """The hidden grader must be red until the task is actually done.

    A grader that exits 0 on unmodified code passes a model that did nothing,
    which makes it indistinguishable from a grader that works. The exit code
    just has to be non-zero — a collection error from an import that does not
    exist yet is as legitimate a failure here as a failed assertion.
    """
    repo = _copy_fixture(case, tmp_path)
    graded = repo / "test_graded.py"
    shutil.copyfile(case / "grade" / "test_graded.py", graded)
    result = _run_pytest([str(graded)], repo)
    assert result.returncode != 0, (
        f"{case.name}: hidden grader passes on the untouched fixture, so it cannot"
        f" tell a finished task from an untouched one\n{result.stdout}{result.stderr}"
    )
