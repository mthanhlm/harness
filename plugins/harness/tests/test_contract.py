"""Parsing the contract, which is what decides how much of the fence exists.

`scoped_files` is the whole scope fence: a path it fails to return is a file the
end-of-turn gate will call an unagreed change, and a section it stops reading too
early takes every remaining agreed file with it. Both failures are silent, and
the gate goes on reporting clean either way.
"""

from __future__ import annotations

import io
import re
from contextlib import redirect_stdout

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


def test_a_scope_section_at_the_end_of_the_file_is_still_a_fence():
    """The section regex required a *following* `## ` heading, so a contract whose
    Scope was its last section parsed as zero files.

    Zero is not a small error. `stop_gate._out_of_scope` returns early on an empty
    list, so an empty fence is indistinguishable from a fence nothing violated —
    every edit in the session gets certified as agreed, and the gate reports clean.
    Silent, and in the direction that licenses unapproved work.
    """
    plan = PLAN.split("## Reuse")[0]

    assert Contract(plan).scoped_files == ["src/one.py", "src/two.py", "src/three.py"]


@pytest.mark.parametrize("heading", [
    "## Scope",
    "## Scope (files this will change)",
    "## Scope — what this touches",
    "### Scope",
    "##  Scope",
])
def test_the_scope_heading_is_read_however_the_plan_writes_it(heading):
    """Same failure, reached by a different route: the heading had to be exactly
    `## Scope` with nothing after it, so a model that added a helpful qualifier
    emptied the fence without any signal that it had."""
    plan = PLAN.replace("## Scope", heading, 1)

    assert Contract(plan).scoped_files == ["src/one.py", "src/two.py", "src/three.py"], (
        f"{heading!r} emptied the fence"
    )


def test_a_backticked_path_without_an_extension_is_scope():
    """`Dockerfile`, `Makefile` and `.env` have no extension, and the entry pattern
    required one — so a plan that scoped its Dockerfile had its own Dockerfile
    reported back as an unagreed change. Backticks are the plan saying "this is a
    path", which is enough to accept it without one."""
    plan = PLAN.replace("- src/one.py — first", "- `Dockerfile` — add a build stage")
    scoped = Contract(plan).scoped_files

    assert "Dockerfile" in scoped
    assert scoped == ["Dockerfile", "src/two.py", "src/three.py"]


def test_a_prose_bullet_is_still_not_a_path():
    """The reason the extension was required in the first place. Loosening it must
    not start reading the plan's own sentences as files, which would fill the fence
    with names no edit can ever match and bury the real strays in noise."""
    plan = PLAN.replace(
        "- src/one.py — first",
        "- Nothing under tests is expected to move\n- src/one.py — first",
    )

    assert Contract(plan).scoped_files == ["src/one.py", "src/two.py", "src/three.py"]


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


def test_the_contract_section_parser_is_not_copied_anywhere_else():
    """There were three of these and they disagreed.

    `contract.scoped_files` required a *following* `## ` heading, so a Scope
    section at the end of the file parsed as zero files. `session_end._section`
    and `post_compact._section` handled that correctly and instead required the
    heading line to be exactly `## Scope`, so a qualifier emptied them. Each copy
    had fixed the bug the others still had, and every one of the three failures is
    silent — an empty scope fence certifies unagreed edits, an empty section drops
    the roadmap entry, an empty carry unbinds the plan at compaction.

    This is the check that keeps the fix from being undone by a fourth copy.
    """
    import re as re_mod
    from pathlib import Path

    scripts = Path(__file__).resolve().parent.parent / "scripts"
    pattern = re_mod.compile(r"\^#\#?\\s\*\{?re\.escape")

    offenders = []
    for path in sorted(scripts.glob("*.py")):
        if path.name == "contract.py":
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(path.name)

    assert not offenders, (
        f"{offenders} re-implement the contract heading parser; call"
        " `contract.section` instead so a fix lands in one place"
    )


# --------------------------------------------------------- naming the file


def test_the_path_command_prints_what_the_hooks_read(data_dir, monkeypatch):
    """The one file `contract.load` opens, named by a command a shell can run.

    Three skills used to name it `${CLAUDE_PLUGIN_DATA}/contracts/${CLAUDE_SESSION_ID}.md`.
    The first half is a placeholder Claude Code substitutes into skill text; the
    second is neither a placeholder nor a variable any shell defines, so it
    arrived verbatim — real transcripts on this machine contain 225 commands
    naming a literal `contracts/${CLAUDE_SESSION_ID}.md`.

    Getting it wrong is silent in the worst direction: a plan written under any
    other name is a file nothing opens, so `approved` is False, `scoped_files` is
    empty, and the end-of-turn gate certifies every edit in the session as agreed.
    """
    import contract as contract_mod

    monkeypatch.setenv(contract_mod.SESSION_ID_ENV, "abc-123")
    out = io.StringIO()
    with redirect_stdout(out):
        code = contract_mod.main(["path"])

    assert code == 0
    assert out.getvalue().strip() == str(contract_mod.contract_path("abc-123"))
    assert str(data_dir) in out.getvalue(), "resolved a directory the hooks do not use"


def test_the_path_command_refuses_rather_than_guesses(data_dir, monkeypatch, capsys):
    """With no session id there is no right answer, and both wrong ones cost.

    A made-up name writes a contract no gate reads. The newest file in the
    directory belongs to whichever session wrote last, which on a second terminal
    is somebody else's plan — and adopting it silently would fence this session
    against another's file list.
    """
    import contract as contract_mod

    monkeypatch.delenv(contract_mod.SESSION_ID_ENV, raising=False)
    out = io.StringIO()
    with redirect_stdout(out):
        code = contract_mod.main(["path"])

    assert code != 0, "printed a path it had no way to know"
    assert out.getvalue().strip() == "", "a caller doing `cat $(...)` would read that as a path"


def test_no_skill_names_a_variable_that_does_not_exist():
    """The structural half, so this cannot come back under a new spelling.

    `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA` and `CLAUDE_PROJECT_DIR` are the
    three placeholders Claude Code substitutes into skill and agent content.
    Anything else shaped like one is a variable the shell does not define, and it
    reaches the model as text.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    allowed = {"CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR"}
    pattern = re.compile(r"\$\{?(CLAUDE_[A-Z_]+)\}?")

    offenders = []
    for path in sorted([*root.glob("skills/*/SKILL.md"), *root.glob("agents/*.md")]):
        for name in set(pattern.findall(path.read_text(encoding="utf-8"))) - allowed:
            offenders.append(f"{path.parent.name}/{path.name}: ${name}")

    assert not offenders, (
        f"{offenders} name something Claude Code does not substitute — it reaches"
        " the model verbatim and resolves to nothing"
    )
