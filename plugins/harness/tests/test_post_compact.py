"""Putting the plan back after the context is compacted.

Compaction is where a long session quietly stops being bound by its plan. What
survives is a summary of the conversation, and a summary keeps the narrative and
drops the constraints — the file fence, the budget, the exclusion list. Nothing
errors; the work simply carries on against a plan nobody holds any more.

That is the specific moment this plugin's whole diagnosis points at. Sessions
past 200 turns run 133 lines changed per file touched against 10 for short ones,
and compaction sits in the middle of every one of them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import contract as contract_mod
import post_compact

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

APPROVED = """# Plan: tighten the scope fence

status: approved
verdict: patch

## Goal
The fence silently drops files it cannot parse.

## Scope
Files this will change:
- `scripts/contract.py` — accept more heading spellings

Explicitly NOT changing:
- `scripts/stop_gate.py` — the caller is fine

## Budget
~2 files, ~40 lines.

## Verification
Command that proves this works: `pytest tests/test_contract.py`

## Prediction
- If this design is wrong, what breaks first: a heading nobody anticipated
- What would show it: a plan whose excluded files parse as in-scope
"""


def test_the_constraints_a_summary_drops_are_the_ones_put_back():
    """Goal and Data flow are narrative — a summary keeps their sense. Scope,
    Budget and Verification are load-bearing and get dropped, and each one is
    something a later turn is judged against."""
    text = post_compact.reminder(contract_mod.Contract(APPROVED))

    assert text
    for heading in ("Scope", "Budget", "Verification", "Prediction"):
        assert f"## {heading}" in text, f"{heading} was not carried across"
    assert "scripts/contract.py" in text, "the fence itself did not survive"
    assert "NOT changing" in text, "the exclusion list did not survive"


def test_the_plan_is_named_so_the_reminder_is_not_free_floating():
    assert "tighten the scope fence" in post_compact.reminder(contract_mod.Contract(APPROVED))


def test_a_session_with_no_approved_plan_is_left_alone(hook_env, tmp_path):
    """Most sessions have no contract. A hook that lectures on every compaction
    is a hook that gets switched off, and then it is not there for the sessions
    that do have one."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "post_compact.py")],
        input=json.dumps({"session_id": "no-such-session", "hook_event_name": "PostCompact"}),
        capture_output=True, text=True, timeout=30, env=hook_env,
    )

    assert proc.returncode == 0
    assert proc.stdout.strip() == "", "it spoke up for a session with no plan"


def _run(hook_env, session_id: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "post_compact.py")],
        input=json.dumps({"session_id": session_id, "hook_event_name": "PostCompact"}),
        capture_output=True, text=True, timeout=30, env=hook_env,
    )


def _write_contract(text: str, session_id: str) -> None:
    path = contract_mod.contract_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_pending_plan_is_not_re_injected(hook_env, data_dir):
    """`status: pending` means the user never approved it. Re-injecting a draft
    after compaction presents it as a constraint the session agreed to.

    Asserted end to end rather than on `Contract.approved`. Checking the
    property on the contract object tests `contract.py`; the question here is
    whether *this hook* consults it, and a version that does not still passes
    the object-level assertion.
    """
    _write_contract(APPROVED.replace("status: approved", "status: pending"), "pending-plan")

    proc = _run(hook_env, "pending-plan")

    assert proc.returncode == 0
    assert proc.stdout.strip() == "", "an unapproved draft was presented as agreed"


def test_an_approved_plan_is_re_injected(hook_env, data_dir):
    """The other direction, so the test above cannot pass by the hook doing
    nothing at all."""
    _write_contract(APPROVED, "approved-plan")

    proc = _run(hook_env, "approved-plan")

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "PostCompact"
    assert "scripts/contract.py" in payload["hookSpecificOutput"]["additionalContext"]


def test_a_contract_with_none_of_the_carried_sections_says_nothing():
    """Rather than emitting an empty block, which reads as "there were no
    constraints" instead of "this plan recorded none"."""
    bare = "# Plan: something\n\nstatus: approved\n\n## Goal\nA thing.\n"

    assert post_compact.reminder(contract_mod.Contract(bare)) is None


def test_a_long_contract_drops_whole_sections_rather_than_cutting_one():
    """Half a file fence is worse than one section fewer.

    A truncated fence still reads as the whole fence, so every file below the
    cut looks deliberately out of scope — which is the opposite of what the
    plan said, and it is the reminder itself that would be lying.
    """
    huge = APPROVED.replace(
        "## Verification\nCommand that proves this works: `pytest tests/test_contract.py`",
        "## Verification\n" + ("Command: `pytest`. " * 900),
    )

    text = post_compact.reminder(contract_mod.Contract(huge))

    assert text
    # Every section that appears must appear complete. The Scope fence is the
    # one that matters, so it must survive intact or not at all.
    if "## Scope" in text:
        assert "scripts/contract.py" in text
        assert "NOT changing" in text, "the fence was cut before its exclusion list"
    assert "Command: `pytest`. Command:" not in text, "a section was cut mid-body"


def test_a_single_section_larger_than_the_budget_is_kept_whole():
    """The remaining case, and it is deliberate. If the Scope alone exceeds the
    budget the plan has a hundred files in it and something has already gone
    wrong — but a complete fence is still the more useful of the two answers,
    and half of one is actively misleading."""
    huge = APPROVED.replace(
        "- `scripts/contract.py` — accept more heading spellings",
        "\n".join(f"- `scripts/file_{i}.py` — a change" for i in range(600)),
    )

    text = post_compact.reminder(contract_mod.Contract(huge))

    assert "scripts/file_0.py" in text
    assert "scripts/file_599.py" in text, "the fence was truncated"
    assert "NOT changing" in text


def test_a_qualified_heading_still_carries_the_fence_through_compaction():
    """`_section` was a third private copy of the same parser and required the
    heading line to be exactly `## Scope`.

    A plan writing `## Scope (files this will change)` therefore carried no scope
    list through the compaction. If every heading in `CARRY` is qualified that
    way `reminder` returns None and the hook emits nothing at all — which looks
    exactly like a session with no approved plan. The compaction this file exists
    to survive becomes the one that unbinds the session from its fence.
    """
    plan = (
        "# Plan: Qualified headings\n\nstatus: approved\n\n"
        "## Scope (files this will change)\n\n- src/one.py — first\n\n"
        "## Budget — forecast\n\n~2 files, ~40 lines.\n\n"
        "## Verification — how we will know\n\npython -m pytest\n"
    )

    text = post_compact.reminder(contract_mod.Contract(plan))

    assert text is not None, "the whole reminder vanished"
    assert "src/one.py" in text, text
    assert "~2 files" in text, text
    assert "python -m pytest" in text, text
