"""Parsing the contract, which is what decides how much of the fence exists.

`scoped_files` is the whole scope fence: a path it fails to return is a file the
end-of-turn gate will call an unagreed change, and a section it stops reading too
early takes every remaining agreed file with it. Both failures are silent, and
the gate goes on reporting clean either way.
"""

from __future__ import annotations

import pytest

from contract import Contract

PLAN = """# Plan: something

status: approved
verdict: patch

## Scope

Files this will change:
- src/one.py — first
- src/two.py — second, and this bullet quotes "Explicitly NOT changing" mid-sentence
- src/three.py — third

Explicitly NOT changing:
- src/excluded.py — deliberately left alone

## Reuse

- Reusing `helper()` at src/other.py
"""


def test_a_plan_that_quotes_its_own_exclusion_heading_keeps_every_file():
    """The bug this was written for.

    An unanchored search for "not chang" matched the phrase inside the second
    bullet, so the list stopped there. One real plan fenced 5 of its 15 files
    and the other 10 were reported as unagreed changes at the end of the turn.
    """
    scoped = Contract(PLAN).scoped_files

    assert scoped == ["src/one.py", "src/two.py", "src/three.py"]


def test_the_excluded_list_is_still_not_scope():
    """The cutoff has to keep working, or the fence swallows the exclusions.

    If the anchor were wrong in the other direction the parser would run past
    the heading and treat deliberately-excluded files as agreed — which is the
    more dangerous half, since it licenses edits nobody approved.
    """
    assert "src/excluded.py" not in Contract(PLAN).scoped_files


def test_reuse_paths_below_the_scope_section_are_not_scope():
    """`## Scope` ends at the next heading, not at the end of the file."""
    assert "src/other.py" not in Contract(PLAN).scoped_files


SPELLINGS = [
    "Explicitly NOT changing:",
    "**Explicitly NOT changing:**",
    "*Explicitly NOT changing*:",
    "### Explicitly NOT changing",
    "## Explicitly NOT changing",
    "> NOT changing",
    "Not changing:",
    "NOT changing (explicitly):",
    "Explicitly not changing:",
    "Deliberately NOT changing:",
    "  Explicitly NOT changing:",
]


@pytest.mark.parametrize("heading", SPELLINGS)
def test_every_way_a_plan_writes_the_exclusion_heading_cuts_the_fence(heading):
    """The dangerous direction, and the one a first fix got wrong.

    Anchoring the cutoff to a line start fixed a truncation bug and introduced a
    worse one: the pattern accepted two exact spellings, so `### Explicitly NOT
    changing` and `Not changing:` stopped matching and their bullets were parsed
    as *in scope*. The plan says it will not touch a deploy workflow, the user
    approves, the agent edits it, and the gate reports clean — it certifies the
    violation.

    Failing to cut is unsafe; cutting too early is merely noisy. This asserts
    both halves on every spelling.
    """
    plan = PLAN.replace("Explicitly NOT changing:", heading)
    scoped = Contract(plan).scoped_files

    assert "src/excluded.py" not in scoped, f"{heading!r} let an excluded file into scope"
    assert scoped == ["src/one.py", "src/two.py", "src/three.py"], (
        f"{heading!r} cut the agreed list short"
    )


def test_an_exclusion_named_mid_bullet_does_not_cut():
    """The other direction, on the widened pattern.

    Loosening the prefix must not go so far that a bullet *mentioning* the
    phrase truncates the list again — that is the bug the anchor was added for.
    """
    plan = PLAN.replace(
        "- src/three.py — third",
        "- src/three.py — third, and we are NOT changing its callers",
    )

    assert Contract(plan).scoped_files == ["src/one.py", "src/two.py", "src/three.py"]
