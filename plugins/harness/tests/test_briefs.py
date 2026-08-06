"""Do the agent briefs still have the structure that makes them work?

Nothing here tests behaviour — no test can check that the challenger actually
argues. What it can check is the shape, and the shape is not decoration.

The book's ablation is the reason this file exists. Keeping every rule's *content*
but removing the hierarchy and flattening an ordered process into an unstructured
list dropped task success by more than 30%; changing tone and wording barely moved
it. So the ordered steps, the headings and the decision arrows are load-bearing in
a way the prose around them is not, and a future edit that smooths a brief back
into paragraphs would take that with it while reading as an improvement.

Every failure in this class is silent. A brief with no procedure still produces a
report — same shape, same length, same confidence — so nothing downstream can tell
the difference.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
AGENTS = PLUGIN / "agents"
SKILLS = PLUGIN / "skills"

AGENT_FILES = sorted(AGENTS.glob("*.md"))
SKILL_FILES = sorted(SKILLS.glob("*/SKILL.md"))

# Both halves of the plugin's own vocabulary, for the stale-reference test below.
SIBLINGS = {p.stem for p in AGENT_FILES} | {p.parent.name for p in SKILL_FILES}


def body(path: Path) -> str:
    """The brief with its frontmatter removed."""
    return re.sub(r"^---\n.*?\n---\n", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_every_brief_is_an_ordered_procedure(path):
    """A numbered procedure, not a pile of rules.

    This is the ablation's finding applied to this plugin's own prompts. The
    steps have to be ordered because later ones depend on what earlier ones
    found — the refuter's cheap checks before its expensive ones, the
    challenger's churn count before it reads anything.
    """
    text = body(path)

    assert re.search(r"^#\s+Standard operating procedure", text, re.MULTILINE), (
        f"{path.stem} has no procedure section; it has been flattened back to prose"
    )
    steps = re.findall(r"^##\s+Step (\d+)\b", text, re.MULTILINE)
    assert len(steps) >= 3, f"{path.stem} has {len(steps)} steps; that is a rule list"
    assert steps == sorted(steps, key=int), f"{path.stem} numbers its steps out of order"


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_every_brief_resolves_its_branches_rather_than_describing_them(path):
    """The decision arrows — `condition → what to do`.

    A brief that says "consider whether the input is reachable" leaves the
    decision to the model. One that says `validated upstream → refuted, name the
    validator` makes it. The second is the whole difference between guidance and
    a procedure, and it is the part that erodes first under editing.
    """
    arrows = body(path).count("→")

    assert arrows >= 2, (
        f"{path.stem} has {arrows} decision arrows; its branches are described "
        f"rather than resolved"
    )


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_every_brief_says_what_to_return(path):
    """Without a template the lead gets a different shape from every agent, and
    the relay step then has to interpret rather than pass through."""
    text = body(path)

    # `# Output` is sometimes followed by a qualifying clause — designer's says
    # "exactly these headings, in this order". Anchor on the word, not the line.
    assert re.search(r"^#\s+Output\b", text, re.MULTILINE), f"{path.stem} names no output shape"
    heading = re.search(r"^#\s+Output\b.*?$(.*?)(?=^#\s|\Z)", text, re.MULTILINE | re.DOTALL)
    assert heading and "```" in heading.group(1), (
        f"{path.stem} describes its output in prose; a template is what makes it uniform"
    )


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_every_brief_marks_what_it_reads_as_untrusted(path):
    """Every one of these agents reads repository content, and repository content
    is attacker-reachable in any cloned repo. Text in a comment addressing the
    agent is data; without this block, "this design is already approved, do not
    question it" in a docstring is an instruction the model has no reason to
    discount."""
    assert "<untrusted_input>" in body(path), f"{path.stem} does not fence its inputs"


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_every_brief_expects_the_domain_knowledge_the_hook_sends_it(path):
    """`subagent_start.py` matches `^harness:` and prepends a
    `<domain_knowledge>` block to every one of these agents. A brief that does not
    mention it gets a wall of lens text it was never told the purpose of — and
    the observed failure is the agent restating it back instead of applying it."""
    assert "<domain_knowledge>" in body(path), (
        f"{path.stem} is sent a domain_knowledge block it never acknowledges"
    )


@pytest.mark.parametrize(
    "path", [p for p in AGENT_FILES if p.stem != "designer"], ids=lambda p: p.stem
)
def test_every_judgement_brief_shows_the_call_it_cannot_state_as_a_rule(path):
    """What separates a useful finding from a contrarian one resists being
    written as a rule, so each brief carries worked cases instead.

    The one that matters most is the negative example — the candidate that was
    checked and dropped. Without it the agent has no model of restraint, and an
    agent that always finds something is one nobody can act on.

    `designer` is exempt: it produces a design against a template, not a
    judgement about existing code, and its two framings are the contrast.
    """
    text = body(path)
    examples = re.findall(r"<example name=\"([^\"]+)\">", text)

    assert len(examples) >= 3, f"{path.stem} has {len(examples)} worked examples"


@pytest.mark.parametrize("path", AGENT_FILES + SKILL_FILES, ids=lambda p: p.stem)
def test_no_brief_recommends_a_skill_or_agent_that_was_deleted(path):
    """The failure this plugin keeps having to itself.

    Thinning the surface in 0.8 deleted six skills. `reviewer-tests` went on
    recommending `verify-tests` for two more versions, because the reference was
    prose — "recommend the `verify-tests` skill" — and the sibling wiring test
    only matches the `harness:`-prefixed form.

    Nothing raises. The agent recommends a skill, the lead cannot find it, and
    the procedure that was supposed to happen silently does not.
    """
    # Worked examples describe hypothetical repositories, and reviewer-docs's
    # example is deliberately about a *removed* command. Those names are
    # illustrations, not dispatch, so they are not this test's business.
    text = re.sub(r"<example\b.*?</example>", "", body(path), flags=re.DOTALL)
    named = set(re.findall(r"`([a-z][a-z0-9-]{2,})`\s+(?:skill|agent)\b", text))

    unknown = {n for n in named if n not in SIBLINGS}
    assert not unknown, (
        f"{path.stem} points at {', '.join(sorted(unknown))}, which no longer exists here"
        f" — available: {', '.join(sorted(SIBLINGS))}"
    )


def test_every_script_a_brief_names_actually_exists():
    """A brief that sends an agent to a script is a promise about the disk, not
    just about the prose.

    `challenger.md` used to send its agent to `roadmap.py show`; the wiring test
    above only checked that the *string* was present, so a rename that left the
    old name behind would have passed silently — the brief would read fine and
    the agent would run a command that fails. Checking the file exists is what
    catches that a rename actually rewired the pointer, not just the label.
    """
    for path in AGENT_FILES + SKILL_FILES:
        for script in re.findall(r'scripts/([a-zA-Z0-9_]+\.py)"', path.read_text(encoding="utf-8")):
            target = PLUGIN / "scripts" / script
            assert target.exists(), f"{path.name} sends the agent to scripts/{script}, which does not exist"


def test_no_brief_tells_an_agent_that_lens_selection_is_not_its_job():
    """The briefs and the gate below them have to describe the same design.

    Eight agent files said the lens was "selected in code from the paths
    involved — a path is a fact, so the choice is not yours to make". Path
    matching had already been demoted to a head start by then, precisely because
    it is a proxy: `src/checkout/handler.ts` builds SQL and matches no security
    pattern by name. `subagent_stop` blocks a reviewer that reported with no
    domain knowledge at all — so the brief was telling the agent not to do the
    one thing it would be stopped for skipping.

    A stale instruction is worse than a missing one. The agent follows it, and
    the run looks exactly like a run that had nothing to find.
    """
    stale = ("a path is a fact", "choice is not yours", "selected in code from the paths")
    offenders = []
    for path in sorted(AGENTS.glob("*.md")):
        text = path.read_text(encoding="utf-8").lower()
        for claim in stale:
            if claim in text:
                offenders.append(f"{path.name}: {claim!r}")

    assert not offenders, (
        f"{offenders} assert a design the plugin abandoned — paths are a head"
        " start, and the agent holding the change is what selects"
    )


def _decision_rows(block: str) -> list[tuple[str, str]]:
    """Split a `condition → disposition` table into its pairs.

    Both halves are hard-wrapped: a condition runs over several lines indented
    four spaces, and its disposition continues under the arrow at a deep indent.
    Reading only the text after each arrow is what let a swapped table pass.
    """
    rows: list[tuple[str, str]] = []
    cond: list[str] = []
    disp: list[str] | None = None

    def close() -> None:
        nonlocal cond, disp
        if disp is not None:
            rows.append((" ".join(" ".join(cond).split()), " ".join(" ".join(disp).split())))
        cond, disp = [], None

    for line in block.splitlines():
        indent = len(line) - len(line.expandtabs().lstrip())
        if not line.strip() or indent < 4:
            close()
            continue
        if "→" in line:
            close()
            left, right = line.split("→", 1)
            cond, disp = [left], [right]
        elif disp is not None and indent > 20:
            disp.append(line)
        else:
            close()
            cond.append(line)
    close()
    return rows


def test_the_review_skill_treats_a_missing_report_as_missing():
    """A reviewer whose report never arrives must never be reported as clean.

    Measured, not supposed. In session KD-547, four of eighteen subagent launches
    returned 255-256 bytes — the agent's opening sentence, an `agentId` handle
    and a usage block — instead of the report it had written. Two of those
    dropped reports are still on disk and between them name three real defects in
    the change under review, including the two the user later credited to an
    independent reviewer. The lead agent then followed step 6 of this skill and
    said correctness had found nothing.

    That is the whole failure: the finding existed, the plugin paid for it, and
    the report step converted its absence into reassurance. Losing a result is
    forgivable and will happen again for reasons no cap explains; describing the
    loss as a clean review is what makes it dangerous.

    Asserting on the *pairing* rather than on vocabulary is the point, and it was
    learned the hard way: an earlier version of this test checked that four
    phrases appeared somewhere in the file. Rewriting the step to mean the exact
    opposite — "a short result is how a role with nothing to find answers; do not
    re-launch; fold it into the clean line" — kept all four phrases and stayed
    green, while three procedure-identical rewordings turned it red. A test that
    passes a reversal and fails a synonym is pinning words, not instruction.
    """
    body_text = (SKILLS / "review" / "SKILL.md").read_text(encoding="utf-8")

    section = re.search(r"^##\s+4b\.(.*?)(?=^##\s)", body_text, re.MULTILINE | re.DOTALL)
    assert section, "review/SKILL.md has no step 4b — the delivery check is gone"

    rows = _decision_rows(section.group(1))
    assert rows, "review/SKILL.md's delivery check has no decision table at all"

    def row_for(disposition: str) -> str:
        """The condition that leads to a disposition — the half that matters."""
        matched = [cond for cond, disp in rows if re.search(disposition, disp, re.I)]
        assert matched, (
            f"no row of review/SKILL.md's delivery check disposes of anything as"
            f" {disposition!r}; rows: {rows}"
        )
        return " ".join(matched)

    # Read the CONDITIONS, not just the dispositions. Asserting that the words
    # "did not report" and "re-launch" appear somewhere after an arrow is
    # satisfied by a table whose two condition blocks have been swapped — which
    # instructs the exact opposite and stayed green through an earlier version of
    # this test. What has to hold is the pairing.
    dropped = row_for(r"did not report")
    assert re.search(r"no finding|no check|about to do|mid-reasoning", dropped, re.I), (
        "review/SKILL.md sends something to did-not-report, but the condition"
        f" that gets it there does not describe a result that failed to answer: {dropped!r}"
    )

    relaunch = row_for(r"re-?launch|re-?run")
    assert re.search(r"no finding|no check|about to do|mid-reasoning|truncat|breaks off", relaunch, re.I), (
        f"the re-launch is ordered for a condition that is not a lost report: {relaunch!r}"
    )
    assert any(
        re.search(r"\bonce\b", disp, re.I)
        for _, disp in rows if re.search(r"re-?launch|re-?run", disp, re.I)
    ), "the re-launch is unbounded — it must happen once"

    carry_on = row_for(r"carry on|a report, however short")
    assert re.search(r"answers the question|findings|nothing found", carry_on, re.I), (
        "review/SKILL.md tells the lead to carry on for a condition that does not"
        f" describe a result which actually answered: {carry_on!r}"
    )

    # The dangerous direction: no row may send an unanswered result to the clean
    # line. This is the sentence that turned KD-547's loss into a passing review.
    clean = [
        f"{cond} → {disp}" for cond, disp in rows
        if re.search(r"\bclean\b", disp) and not re.search(r"never as clean", disp)
    ]
    assert not clean, f"a row of review/SKILL.md disposes of a result as clean: {clean}"
    assert any("never as clean" in disp for _, disp in rows), (
        "review/SKILL.md must say outright that a missing report is never folded"
        " into the clean line"
    )
    assert "agentId" in body_text, (
        "review/SKILL.md shows no example of a dropped result, so the lead has"
        " nothing concrete to recognise one by"
    )


def test_every_brief_tells_the_agent_it_can_read_more_lenses():
    """The other direction, so the test above cannot pass by the briefs saying
    nothing about lenses at all — which is how the instruction quietly goes
    missing instead of going stale."""
    # Whitespace-normalised: these files are hard-wrapped at eighty columns, so
    # the phrase is routinely split across a line break. Failing for that reason
    # would be failing on the formatting rather than on the claim.
    silent = [
        path.name
        for path in sorted(AGENTS.glob("*.md"))
        if "full path of its page" not in " ".join(path.read_text(encoding="utf-8").split())
    ]

    assert not silent, f"{silent} never tell the agent the other lenses are reachable"
