"""Can a reviewer finish without ever having domain knowledge in front of it?

The answer used to be yes, silently, and that is the whole reason this exists.

Selection was path matching, which is a proxy: `src/checkout/handler.ts` builds
SQL and matches no security pattern by name; `internal/store.go` runs a migration
and matched nothing at all. Both got reviewed with no lens, and a review written
without one has the same shape, length and confidence as one written with it — so
nothing downstream could tell.

Matching harder was the wrong answer and was tried first. The thing that can tell
`store.go` is running a migration is the agent holding the diff. So the agent
selects, and this gate is what makes that a requirement instead of a line in a
brief that gets skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

import lens_gate


def _transcript(tmp_path: Path, *tool_calls: dict) -> str:
    """A transcript in the shape the hook actually receives: JSONL, one event
    per line, tool calls nested inside an assistant message."""
    path = tmp_path / "transcript.jsonl"
    lines = [json.dumps({"type": "user", "message": {"content": "review this"}})]
    for call in tool_calls:
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": call["name"], "input": call["input"]}]},
        }))
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def test_a_reviewer_with_no_lens_at_all_is_blocked():
    """The case the whole gate is for, and the one that was silent."""
    assert lens_gate.verdict("reviewer-correctness", injected=[], read=set())


def test_a_reviewer_that_was_given_a_lens_is_left_alone():
    """The point is not to make agents perform a Read. It is that no domain
    judgement gets made with nothing in front of it — and an injected lens is in
    front of it."""
    assert lens_gate.verdict("reviewer-correctness", ["lens-python"], set()) is None


def test_a_reviewer_that_went_and_read_one_is_left_alone():
    """The path matched nothing, the agent worked out what the change was about
    and read the page. That is the behaviour being asked for, so it must not be
    what gets punished."""
    assert lens_gate.verdict("reviewer-perf", [], {"lens-database"}) is None


def test_an_agent_that_is_not_a_reviewer_is_never_blocked():
    """The worker builds to a brief and the refuter attacks one claim. Neither
    produces a domain judgement whose quality silently depends on a lens, and a
    gate that fires on everything is a gate that gets switched off."""
    for agent in ("worker", "refuter", "challenger", "designer"):
        assert lens_gate.verdict(agent, [], set()) is None, agent


def test_every_reviewer_is_covered_by_the_gate():
    """The other direction, so the test above cannot pass by the gate covering
    nothing. Asserted against the agent files rather than a hand-written list,
    which would drift the first time a reviewer is added."""
    agents = Path(__file__).resolve().parent.parent / "agents"
    reviewers = [p.stem for p in agents.glob("reviewer-*.md")]

    assert len(reviewers) >= 6, "the reviewers moved; this test is looking in the wrong place"
    for name in reviewers:
        assert lens_gate.is_gated(name), f"{name} can finish with no domain knowledge"


def test_the_block_reason_tells_the_agent_what_to_do():
    """A block that does not say how to clear it is an infinite loop with a
    polite message. It has to name where the pages are."""
    reason = lens_gate.verdict("reviewer-security", [], set())

    assert "references/lenses/" in reason
    assert "no lens applies" in reason, "no way to answer honestly that none apply"


def test_the_block_reason_names_a_directory_that_exists_on_disk():
    """It used to name `${CLAUDE_PLUGIN_ROOT}/references/lenses/<name>.md`.

    Claude Code substitutes that placeholder into skill and agent *content* and
    into hook *commands* — never into what a hook prints. So an agent was blocked
    for not reading a page and then handed a path it had no way to expand, from a
    shell where no such variable exists. The block could not be cleared by doing
    what it asked.
    """
    reason = lens_gate.verdict("reviewer-security", [], set())

    assert "${" not in reason, "an unexpanded placeholder reached the agent"
    directory = Path(reason.split("`")[1].replace("<name>.md", ""))
    assert directory.is_dir(), f"the block names {directory}, which does not exist"
    assert (directory / "lens-security.md").is_file(), (
        "the named directory exists but the pages are not in it"
    )


def test_a_read_of_a_lens_page_is_found_in_the_transcript(tmp_path):
    """Read from the transcript rather than from the agent's prose. An agent
    that says "applying the security lens" without opening it leaves no tool
    call behind, and that is exactly the case worth catching."""
    path = _transcript(
        tmp_path,
        {"name": "Grep", "input": {"pattern": "def handler"}},
        {"name": "Read", "input": {"file_path": "/plugins/harness/references/lenses/lens-database.md"}},
    )

    assert lens_gate.lenses_read(path) == {"lens-database"}


def test_reading_ordinary_files_is_not_mistaken_for_reading_a_lens(tmp_path):
    """Otherwise every agent passes the gate by reading the diff, and the gate
    is decorative."""
    path = _transcript(
        tmp_path,
        {"name": "Read", "input": {"file_path": "/repo/src/checkout/handler.ts"}},
        {"name": "Read", "input": {"file_path": "/repo/docs/lenses-explained.md"}},
    )

    assert lens_gate.lenses_read(path) == set()


def test_an_ordinary_read_beside_a_lens_read_on_one_line_is_not_counted(tmp_path):
    """The discriminating case, and the previous test does not reach it.

    A cheap prefilter skips any transcript line without the lens directory in
    it, so a message containing only ordinary reads never reaches the per-path
    check at all. The check only earns its place when *one* message holds both —
    which is the normal shape, since an assistant turn can carry several tool
    calls. Without this fixture, deleting that check leaves every test green.
    """
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": "Read",
             "input": {"file_path": "references/lenses/lens-security.md"}},
            {"type": "tool_use", "name": "Read",
             "input": {"file_path": "/repo/src/checkout/handler.ts"}},
        ]},
    }), encoding="utf-8")

    assert lens_gate.lenses_read(str(path)) == {"lens-security"}, (
        "an ordinary file read in the same message was counted as a lens"
    )


def test_a_missing_or_unreadable_transcript_does_not_raise(tmp_path):
    """This runs on a hook that decides whether an agent may finish. An
    exception here is worse than a wrong answer."""
    assert lens_gate.lenses_read(None) == set()
    assert lens_gate.lenses_read(str(tmp_path / "nope.jsonl")) == set()


def test_a_transcript_line_that_is_not_json_is_skipped(tmp_path):
    """The file is written asynchronously, so a truncated final line is normal
    rather than exceptional."""
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read",
             "input": {"file_path": "references/lenses/lens-security.md"}}]}})
        + '\n{"type": "assistant", "message": {"content": [{"input": {"file_pa',
        encoding="utf-8",
    )

    assert lens_gate.lenses_read(str(path)) == {"lens-security"}


def test_a_changed_transcript_shape_still_finds_the_read(tmp_path):
    """The transcript is a private format that has changed before. Indexing a
    known shape would silently find nothing after a change — and "found nothing"
    here means "block every reviewer", which is the loudest possible way to be
    wrong about the quietest possible cause.
    """
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({
        "type": "assistant",
        "some": {"future": {"nesting": [{"tool": {"input": {
            "file_path": "references/lenses/lens-infra.md"}}}]}},
    }), encoding="utf-8")

    assert lens_gate.lenses_read(str(path)) == {"lens-infra"}


# --------------------------------------- which transcript holds the evidence


def test_the_agents_own_transcript_is_the_one_read(tmp_path):
    """A `SubagentStop` payload carries two transcripts and they are not the same.

    `transcript_path` is the *main session's* file. The subagent's own tool calls
    are in `agent_transcript_path`, under a nested `subagents/` directory — this
    gate was reading the first, where a subagent's Read of a lens page can never
    appear.

    So the evidence side of the gate was dead: `lenses_read` returned the empty
    set no matter what the agent had done, and the only thing standing between a
    compliant reviewer and a block was whether something happened to be injected.
    """
    main = _transcript(tmp_path, {"name": "Read", "input": {"file_path": "/repo/src/app.ts"}})
    own = tmp_path / "subagents"
    own.mkdir()
    agent = _transcript(own, {"name": "Read", "input": {"file_path": "/p/references/lenses/lens-security.md"}})

    chosen = lens_gate.agent_transcript(
        {"transcript_path": main, "agent_transcript_path": agent}
    )

    assert chosen == agent, "read the main session's transcript, not the agent's"
    assert lens_gate.lenses_read(chosen) == {"lens-security"}


def test_a_reviewer_that_read_its_lens_is_not_blocked(tmp_path):
    """The end the fix is actually for. Nothing was injected — no path in the
    change matched — and the agent did exactly what the catalogue asked. Blocking
    it teaches that complying changes nothing, which is how a gate gets removed.
    """
    own = tmp_path / "subagents"
    own.mkdir()
    event = {
        "transcript_path": _transcript(tmp_path),
        "agent_transcript_path": _transcript(
            own, {"name": "Read", "input": {"file_path": "/p/references/lenses/lens-database.md"}}
        ),
    }

    read = lens_gate.lenses_read(lens_gate.agent_transcript(event))

    assert lens_gate.verdict("reviewer-correctness", [], read) is None, (
        "blocked a reviewer for not doing the thing it did"
    )


def test_the_main_transcript_is_still_used_when_there_is_no_agent_one(tmp_path):
    """Falling back rather than reading nothing. If a future payload drops the
    field, the wrong file is no worse than what this replaced; reading none would
    silently re-arm the false block on every reviewer at once."""
    main = _transcript(tmp_path, {"name": "Read", "input": {"file_path": "/p/references/lenses/lens-infra.md"}})

    assert lens_gate.agent_transcript({"transcript_path": main}) == main
    assert lens_gate.agent_transcript({"agent_transcript_path": "  "}) is None


# ------------------------------------------- whose agents this gate applies to


def test_another_plugins_reviewer_is_not_blocked_by_this_one():
    """`SubagentStop` fires for every subagent in the session, not only ours.

    A plugin agent's `agent_type` is scoped, and taking the tail after the colon
    made `harness:reviewer-security` and `otherplugin:reviewer-security` the same
    agent. A second plugin shipping a reviewer would have had its agents stopped
    mid-task by a message about lenses it has never heard of, in a directory
    belonging to a plugin it is not part of.
    """
    foreign = lens_gate.bare_name("otherplugin:reviewer-security")

    assert lens_gate.verdict(foreign, [], set()) is None, "blocked another plugin's agent"


def test_this_plugins_reviewer_is_still_blocked():
    """The other direction, so the test above cannot pass by the gate matching
    nothing at all — which is the cheaper way to make it green."""
    ours = lens_gate.bare_name("harness:reviewer-security")

    assert ours == "reviewer-security"
    assert lens_gate.verdict(ours, [], set()), "our own reviewer stopped being gated"


def test_an_unscoped_name_is_treated_as_ours():
    """A plugin loaded with `--plugin-dir` for development, and the built-ins.
    Dropping the unscoped case would switch the gate off exactly while the plugin
    is being worked on, which is when it is most worth having."""
    assert lens_gate.bare_name("reviewer-perf") == "reviewer-perf"
    assert lens_gate.bare_name("general-purpose") == "general-purpose"
    assert lens_gate.verdict(lens_gate.bare_name("general-purpose"), [], set()) is None
