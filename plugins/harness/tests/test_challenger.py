"""The couplings between the challenger and the flow that consumes it.

Nothing here tests whether the challenger argues well — that is a property of a
prompt and a model, and a test that claimed to check it would be measuring my
own wording against itself. What is testable is the wiring around it, and the
wiring is where this stage dies quietly:

The agent emits piles under specific headings and the plan skill sorts on those
headings. The agent returns one of four verdicts and the contract has a field
that must accept it. Both are two declarations of one fact, written in files
nobody opens together, and both fail without raising — the stage runs, produces
prose, and the prose goes nowhere.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
CHALLENGER = (PLUGIN / "agents" / "challenger.md").read_text(encoding="utf-8")
PLAN = (PLUGIN / "skills" / "plan" / "SKILL.md").read_text(encoding="utf-8")

VERDICTS = ("patch", "refactor-first", "rewrite", "don't build this")


@pytest.mark.parametrize("verdict", VERDICTS)
def test_every_verdict_the_challenger_can_return_is_one_the_contract_can_hold(verdict):
    """The lead is told to carry the verdict into the contract's `verdict:` field.

    A verdict the field does not list is one the lead has to translate, and the
    translation always rounds toward the comfortable answer — which is precisely
    the failure `don't build this` exists to prevent.
    """
    assert verdict in CHALLENGER, f"the challenger cannot return `{verdict}`"

    field = re.search(r"^verdict:\s*(.+)$", PLAN, re.MULTILINE)
    assert field, "the contract template no longer has a verdict field"
    assert verdict in field.group(1), (
        f"the challenger returns `{verdict}` and the contract cannot record it"
    )


def test_dont_build_this_is_not_quietly_downgraded_to_a_last_resort():
    """The one verdict the whole stage exists for, and the one most comfortable
    to soften. It has to be reachable in the brief, not merely listed."""
    assert "don't build this" in CHALLENGER
    assert re.search(r"must stay reachable|reachable", CHALLENGER, re.IGNORECASE), (
        "nothing in the brief protects the verdict that stops work"
    )


def _output_template(text: str) -> str:
    """The fenced block under `## Output` — the shape the agent actually emits.

    Checked against the template rather than the whole file on purpose: the
    prose above it discusses the piles by name too, so a whole-file search
    passes while the emitted headings have been renamed out from under the
    consumer. That was the first mutation this test failed to catch.

    The second was subtler and is why this is anchored to the heading rather
    than to "the last fenced block". An unanchored `` ```\\n(.*?)``` `` matches
    from the *closing* fence of a ```bash block to the *opening* fence of the
    next one — so it returned the prose between two code blocks, and the test
    went on measuring exactly what it was written to stop measuring.
    """
    section = re.search(r"^#{1,3}\s+Output\s*$(.*)", text, re.MULTILINE | re.DOTALL)
    assert section, "the challenger brief has no `Output` section"
    block = re.search(r"^```[^\n]*\n(.*?)^```", section.group(1), re.MULTILINE | re.DOTALL)
    assert block, "the `## Output` section no longer shows the shape to emit"
    return block.group(1)


@pytest.mark.parametrize("pile", ["Blocking", "Advisory"])
def test_the_piles_the_agent_emits_are_the_piles_the_skill_sorts_on(pile):
    """Two files declaring one protocol.

    The agent's output template writes these headings; the plan skill relays by
    them and routes one of them into `AskUserQuestion`. Rename either side and
    the skill is sorting on a heading that never arrives — no error, just an
    empty blocking pile, which reads exactly like a request nobody could argue
    with.
    """
    assert pile in _output_template(CHALLENGER), (
        f"the challenger's output template no longer emits a `{pile}` pile"
    )
    assert pile in PLAN, f"the plan skill no longer knows about the `{pile}` pile"


def test_the_agent_sorts_by_citation_rather_than_by_how_sure_it_feels():
    """The load-bearing distinction, and the one that decays first.

    "Blocking = important" is the natural reading and the wrong one. It puts
    uncited judgement in front of the user as something they must answer; they
    open it, find nothing to check, and learn to click through. One round of
    that and the mechanism is gone.
    """
    assert re.search(r"whether it carries a citation", CHALLENGER, re.IGNORECASE), (
        "the challenger no longer sorts objections by citation"
    )
    # Phrasing-tolerant, concept-strict. "NEVER sort by how sure you feel" and
    # "not by how sure you feel" are the same rule, and the uppercase form is
    # the one the book recommends for a constraint that must not be missed —
    # but "sorts objections somehow" must still fail.
    assert re.search(
        r"(?:never|not)\b[^.\n]{0,20}by how sure you feel|not by confidence",
        CHALLENGER,
        re.IGNORECASE,
    ), "the challenger no longer rules out sorting by confidence"


def test_the_lead_is_forbidden_from_promoting_an_uncited_objection():
    """The agent's own rule is not enough, because the lead is the one holding
    `AskUserQuestion`. An advisory finding it decides is important enough to
    block on defeats the split from the other end."""
    assert re.search(
        r"never promote an advisory objection into the blocking pile", PLAN, re.IGNORECASE
    ), "the plan skill no longer forbids promoting an advisory objection"


def test_the_challenger_runs_on_the_strong_model():
    """A weak planner is the one failure a run cannot recover from — everything
    downstream executes its conclusion faithfully. This stage decides whether
    the right thing gets built at all, so it is the last place to save money;
    the review fan-out below it is where cheaper models belong."""
    frontmatter = re.match(r"^---\n(.*?)\n---", CHALLENGER, re.DOTALL)
    assert frontmatter, "challenger.md has no frontmatter"
    assert re.search(r"^model:\s*opus\s*$", frontmatter.group(1), re.MULTILINE)


def test_the_challenger_is_told_to_go_and_find_evidence_rather_than_re_read():
    """Ch10's finding, and the reason this agent is not just a second opinion: a
    reviewer that consumes only what the proposer already had adds nothing, and
    measured on GPT-4, self-correction without external feedback *lowered*
    accuracy. The brief has to send it to the git log, the roadmap and the code."""
    for probe in ("git log", "roadmap.py", "codegraph"):
        assert probe in CHALLENGER, f"the challenger is never sent to {probe}"
