"""Parsing the contract, which is what decides how much of the fence exists.

`scoped_files` is the whole scope fence: a path it fails to return is a file the
end-of-turn gate will call an unagreed change, and a section it stops reading too
early takes every remaining agreed file with it. Both failures are silent, and
the gate goes on reporting clean either way.
"""

from __future__ import annotations

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


def test_a_bold_exclusion_heading_is_recognised():
    """Plans write it `**Explicitly NOT changing:**` about as often as plain."""
    bold = PLAN.replace("Explicitly NOT changing:", "**Explicitly NOT changing:**")

    assert "src/excluded.py" not in Contract(bold).scoped_files
