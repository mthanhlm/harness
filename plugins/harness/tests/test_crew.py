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
import re
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


def test_every_brief_is_offered_by_the_crew():
    """A brief with no registry entry is an agent the crew report never offers,
    so the skill never launches it and nothing says why. The failure is silent:
    the report is well-formed, and a review missing a whole role looks exactly
    like a review that had nothing to find.

    The other direction — a registered role with no brief — is already covered
    per-role by `test_wiring.test_every_registry_role_has_an_agent_file`, which
    fails by name instead of once for the whole set.

    Read from the file rather than through `crew.registry()`: that helper
    resolves `plugin_root()`, which follows `CLAUDE_PLUGIN_ROOT`. The end-of-turn
    gate sets it to the *installed* copy, so going through the helper compared
    this checkout's briefs against a different tree's registry and failed only
    under the hook. A source-tree invariant has to read one tree.
    """
    plugin = SCRIPTS.parent
    # `worker` is the one agent the crew never selects. `implement` launches it
    # from the slices an approved plan named, so it belongs to no phase and
    # answers no "should this run" question.
    agents = {p.stem for p in (plugin / "agents").glob("*.md")} - {"worker"}
    roles = json.loads((plugin / "crew" / "registry.json").read_text(encoding="utf-8"))["roles"]
    registered = {role["name"] for role in roles}

    assert agents <= registered, f"brief never offered by the crew: {sorted(agents - registered)}"


def test_the_coherence_role_is_conditional_and_the_skill_names_it():
    """It earns its place by being rare. Four of the five findings the user
    valued in KD-547 came from asking whether the change hung together, and no
    role owned that question — but a role that runs on every diff is how a review
    becomes noise, and noise is what gets the whole skill bypassed.

    The skill has to name it in the list the lead actually reads. `crew.py`
    offers conditional roles and something has to choose between them; that
    decision lives in step 3's bullet list, not in the prose around it. Merely
    asserting the name appears somewhere in the file is not enough — verified by
    mutation: deleting the step-3 bullet while leaving the explanatory paragraph
    intact left a substring check green, and a role nothing launches produces a
    review that looks exactly like a review with nothing to find.
    """
    # From the file, not `crew.registry()` — see the note in the test above.
    plugin = SCRIPTS.parent
    roles = json.loads((plugin / "crew" / "registry.json").read_text(encoding="utf-8"))["roles"]
    role = next(r for r in roles if r["name"] == "reviewer-coherence")

    assert role["phase"] == "after"
    assert not role["always"], "an extra role on every review is what gets it skipped"

    skill = (plugin / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8")
    bullet = re.search(r"^-\s+`reviewer-coherence`\s+—\s+(.+)$", skill, re.MULTILINE)

    assert bullet, (
        "review/SKILL.md has no `- `reviewer-coherence` — <when>` bullet in the"
        " conditional list, so the lead is never told to launch it"
    )

    when = bullet.group(1).strip()
    # The bullet has to say when to LAUNCH it. Checking only that some words
    # follow the dash is satisfied by `— never launch this role under any
    # circumstances`, which passed an earlier version of this test while
    # disabling the role the test exists to protect. Every sibling bullet in
    # this list opens `if …` or `only if …`; hold this one to the same shape.
    assert re.match(r"(only )?if\b", when, re.IGNORECASE), (
        f"the bullet does not state a launch condition in the form its siblings"
        f" use (`if …` / `only if …`): {when!r}"
    )
    assert not re.search(r"\bnever\b|\bdo not\b|\bdon't\b", when, re.IGNORECASE), (
        f"the bullet tells the lead not to launch the role: {when!r}"
    )
    assert len(when.split()) >= 4, f"the bullet states no condition to judge against: {when!r}"


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
