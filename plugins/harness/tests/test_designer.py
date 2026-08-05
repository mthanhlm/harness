"""The couplings that make two designs worth more than one.

As with the challenger, nothing here can test whether a design is good. What is
testable is the machinery that turns two designs into a decision, and every part
of it fails silently:

The lead diffs the two answers heading by heading, so the headings are a protocol
between three files. The whole value comes from the two runs being independent
and opposed, so anything that lets them see each other, or lets the lead resolve
their disagreement alone, quietly converts this stage back into one design with
extra steps — at twice the cost, and looking exactly the same from outside.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
DESIGNER = (PLUGIN / "agents" / "designer.md").read_text(encoding="utf-8")
PLAN = (PLUGIN / "skills" / "plan" / "SKILL.md").read_text(encoding="utf-8")

# The headings the lead compares. Each one is a place where two designs can
# disagree about something a single draft would have settled by omission.
HEADINGS = ("Goal", "User flow", "Data flow", "Scope", "Verification", "Prediction")


def _output_template(text: str) -> str:
    """The fenced block under `## Output` — see test_challenger for why this is
    anchored to the heading rather than to "the last fenced block"."""
    section = re.search(r"^#{1,3}\s+Output[^\n]*$(.*)", text, re.MULTILINE | re.DOTALL)
    assert section, "the designer brief has no `Output` section"
    block = re.search(r"^```[^\n]*\n(.*?)^```", section.group(1), re.MULTILINE | re.DOTALL)
    assert block, "the `## Output` section no longer shows the shape to emit"
    return block.group(1)


@pytest.mark.parametrize("heading", HEADINGS)
def test_each_heading_the_lead_diffs_is_one_the_designer_emits(heading):
    """Three declarations of one protocol: the designer writes the heading, the
    lead compares on it, the contract records the result. A heading dropped from
    the designer's template is not an error — it is a point of the design that
    silently stops being compared, and therefore gets decided by nobody."""
    assert heading in _output_template(DESIGNER), (
        f"the designer's output template no longer emits `{heading}`"
    )
    assert heading in PLAN, f"the plan skill no longer diffs on `{heading}`"


def test_the_contract_can_record_the_prediction_the_designers_make():
    """Ch8's falsifiable change contract only works if the prediction survives
    into something durable. A prediction made in a subagent's reply and never
    written down cannot be checked against what happened, which is the entire
    point of making it."""
    assert re.search(r"^##\s+Prediction\s*$", PLAN, re.MULTILINE), (
        "the contract template has no Prediction section, so the designers'"
        " prediction is discarded at the end of the stage"
    )


def test_the_two_runs_are_opposed_rather_than_merely_repeated():
    """Two samples of the same prompt are a retry, not a second opinion. The
    framings have to be stated as opposites in both the brief and the skill, or
    the stage degrades into paying twice for one design."""
    for source, name in ((DESIGNER, "designer brief"), (PLAN, "plan skill")):
        assert "smallest change that could work" in source, (
            f"the {name} no longer states framing A"
        )
        assert "structure this actually needs" in source, (
            f"the {name} no longer states framing B"
        )


def test_the_designers_do_not_see_each_other():
    """The independence is the mechanism, not an implementation detail. A second
    designer given the first's answer can only lose information relative to it —
    it will converge on what it was shown, and the divergence that was the whole
    output disappears."""
    assert re.search(
        r"neither of you will see the other|neither sees the other", DESIGNER + PLAN,
        re.IGNORECASE,
    ), "nothing states that the two designs are drawn independently"
    assert re.search(r"in a single message", PLAN), (
        "the plan skill no longer launches both designers at once, which is what"
        " keeps the second from being handed the first"
    )


def test_the_lead_is_forbidden_from_resolving_the_divergence_alone():
    """The failure this whole plan exists to stop, arriving in a new costume.

    A lead that picks between the two designs and presents the winner has
    rebuilt the dynamic the user complained about — the assistant deciding and
    the user agreeing — while looking like a debate happened. The divergence is
    the output; the choice is the user's.
    """
    assert re.search(
        r"You do not resolve the divergence and neither does a third agent", PLAN
    ), "the plan skill no longer forbids the lead picking a design for the user"
    assert "AskUserQuestion" in PLAN


def test_no_judge_agent_exists():
    """Deliberate, and worth pinning because adding one is the obvious next idea.

    Every model in this plugin is the same family, so a judge shares the blind
    spot that produced the disagreement — it cannot break a tie it is subject
    to. And a judge that picks for the user is the lead's forbidden move with a
    subagent's name on it.
    """
    names = {p.stem for p in (PLUGIN / "agents").glob("*.md")}
    assert not (names & {"judge", "design-judge", "arbiter", "selector"}), (
        f"a judge agent was added: {names & {'judge', 'design-judge', 'arbiter', 'selector'}}"
    )


def test_agreement_between_the_two_designs_is_a_result_and_not_a_failure():
    """Otherwise the incentive is to manufacture a difference, which puts a fake
    choice in front of someone who has already said they cannot always tell a
    good design from a bad one. That is worse than offering no choice."""
    assert re.search(
        r"empty divergence list is a good outcome|two designs that agree everywhere is a"
        r" real and useful result",
        DESIGNER + PLAN,
        re.IGNORECASE,
    ), "nothing tells either side that agreeing is an acceptable answer"


def test_the_designer_runs_on_the_strong_model():
    """The book is blunt that a weak planner is unrecoverable: everything
    downstream executes its conclusion faithfully. Cheaper models belong in the
    review fan-out, where a miss is caught by the next reviewer."""
    frontmatter = re.match(r"^---\n(.*?)\n---", DESIGNER, re.DOTALL)
    assert frontmatter, "designer.md has no frontmatter"
    assert re.search(r"^model:\s*opus\s*$", frontmatter.group(1), re.MULTILINE)


def test_this_stage_does_not_run_on_every_request():
    """Two Opus designs on a rename is precisely the ceremony that gets a flow
    switched off — and a flow nobody runs cannot improve anything."""
    stage = re.search(
        r"^##\s+Stage 1d.*?$(.*?)^##\s", PLAN, re.MULTILINE | re.DOTALL
    )
    assert stage, "the two-design stage is no longer a section of the plan skill"
    assert re.search(r"Route C only", stage.group(1)), (
        "the two-design stage no longer says which route it runs on"
    )
