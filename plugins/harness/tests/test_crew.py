"""Which lenses a job loads.

The division under test: a changed file's path is a fact and is matched here; a
task's subject is a judgement and is deliberately not. This file guards the
first half and guards that the second half is fully *offered* — a lens missing
from the catalogue can never be chosen, however good the judgement reading it.

The keyword matching this replaced is worth remembering, because it looked like
it worked. Matching was by substring, so `ui` fired inside "b*ui*ld", `api`
inside "c*api*tal" and "r*api*d", `auth` inside "*auth*or" — every task
containing the word "build" loaded the frontend lens.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from crew import lens_catalogue, registry, select_lenses

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def lenses(*files: str) -> list[str]:
    return sorted(lens["name"] for lens in select_lenses(list(files)))


@pytest.mark.parametrize(
    "path,expected",
    [
        ("src/db/schema.ts", "lens-database"),
        ("app/page.tsx", "lens-frontend"),
        ("app/api/invoices/route.ts", "lens-backend"),
        ("scripts/detect.py", "lens-python"),
        ("src/lib/money.ts", "lens-typescript"),
        ("tests/test_money.py", "lens-testing"),
        ("src/auth/session.ts", "lens-security"),
        ("Dockerfile", "lens-infra"),
        ("plugins/harness/agents/refuter.md", "lens-llm-agents"),
    ],
)
def test_a_path_selects_its_lens(path, expected):
    assert expected in lenses(path)


def test_a_bare_ts_file_is_no_longer_orphaned():
    """Before `lens-typescript`, `src/lib/money.ts` matched nothing at all —
    `lens-frontend` claims `.tsx`, not `.ts`."""
    assert lenses("src/lib/money.ts") == ["lens-typescript"]


def test_nothing_changed_selects_nothing():
    assert lenses() == []


def test_paths_do_not_guess_from_prose():
    """The old failure, now impossible: prose is not an input to this function."""
    assert lenses("docs/build-a-capital-report.md") == []


def test_the_catalogue_offers_every_lens():
    """A lens absent here cannot be chosen by the judgement that reads it."""
    assert {l["name"] for l in lens_catalogue()} == {l["name"] for l in registry()["lenses"]}
    assert all(l["domain"] for l in lens_catalogue())


def test_the_report_carries_both_halves(tmp_path):
    """The script's output is the whole interface the review skill reads."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "crew.py"), "after", "add an endpoint"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["task"] == "add an endpoint"
    assert "lenses_from_files" in report
    assert [r["name"] for r in report["roles_always"]]
    assert [r["subagent_type"] for r in report["roles_always"]]


def test_the_report_no_longer_ships_the_lens_catalogue():
    """Each agent now loads its own domain knowledge from the paths it is given.
    Shipping the catalogue here made the lead pick lenses on behalf of agents
    that had already picked better — and a second, worse selection mechanism
    competing with a working one is how the first got built."""
    report = json.loads(
        subprocess.run(
            [sys.executable, str(SCRIPTS / "crew.py"), "after", "x"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    )
    assert "lens_catalogue" not in report


def test_a_directory_pattern_matches_that_directory_at_the_repo_root():
    """`**/evals/**` against `evals/cases/a.py`.

    The leading `**/` has nothing to consume when the directory is at the root,
    so fnmatch misses it and so did both of the special cases beside it. Two
    lenses had declared patterns in this shape since before 0.8 and neither had
    ever fired on a top-level directory — silently, because a lens that does not
    load produces a review that looks exactly like a thorough one.
    """
    assert "lens-evaluation" in lenses("evals/cases/a.py")
    assert "lens-evaluation" in lenses("packages/api/evals/cases/a.py")
    assert "lens-resilience" in lenses("jobs/nightly.py")


def test_a_directory_pattern_does_not_match_a_similarly_named_file():
    """`**/jobs/**` is a directory, not a prefix. `jobsboard.ts` is not a job."""
    assert "lens-resilience" not in lenses("src/jobsboard.ts")
    assert "lens-evaluation" not in lenses("src/evaluations.md")
