"""A worker verifies its own slice, and only its own slice.

The failure this guards against is the one that would make parallel workers
worse than serial ones: worker A gets blocked for a file worker B broke, tries
to fix work it did not do, and the two of them spin. Every test here is about
where the blame lands.
"""

from __future__ import annotations

from pathlib import Path

from conftest import run_hook
from state import session_state, shard_path, write_json

SCRIPT = "subagent_stop.py"


def record(writer: str, *paths: Path) -> None:
    """Populate a shard the way the real hook does — through `session_state`.

    Hand-writing the shard would leave the producer untested, and the mutation
    that matters most slips through: drop the delta subtraction so a writer
    records every file it can *see*, and the merged view is still correct
    because it is a union. Only going through the producer exposes it.
    """
    with session_state("sess", writer) as session:
        session["files_touched"] = sorted(
            set(session["files_touched"]) | {str(p) for p in paths}
        )
        session["lines_changed"] = int(session["lines_changed"]) + 1


def payload(agent_id: str | None, repo: Path) -> dict:
    event = {"session_id": "sess", "cwd": str(repo), "hook_event_name": "SubagentStop"}
    if agent_id is not None:
        event["agent_id"] = agent_id
    return event


def test_worker_is_not_blocked_for_another_workers_breakage(data_dir, git_repo, hook_env):
    """The whole reason this hook reads one shard rather than the merged view.

    Worker B records *first*, so when worker A records afterwards it can see
    b.py in the merged view. A writer that stored what it saw instead of what it
    added would put b.py in worker A's shard, and worker A would then be blocked
    for a file it never touched.
    """
    (git_repo / "b.py").write_text("def broken(:\n", encoding="utf-8")
    record("worker-b", git_repo / "b.py")
    record("worker-a", git_repo / "a.py")

    assert run_hook(SCRIPT, payload("worker-a", git_repo), hook_env, git_repo) == {}


def test_worker_is_blocked_for_its_own_breakage(data_dir, git_repo, hook_env):
    (git_repo / "b.py").write_text("def broken(:\n", encoding="utf-8")
    record("worker-b", git_repo / "b.py")

    response = run_hook(SCRIPT, payload("worker-b", git_repo), hook_env, git_repo)

    assert response.get("decision") == "block"
    assert "b.py" in response["reason"]


def test_a_worker_that_wrote_nothing_is_left_alone(data_dir, git_repo, hook_env):
    """Reviewers, the refuter and the architect all land here."""
    assert run_hook(SCRIPT, payload("reviewer-bloat", git_repo), hook_env, git_repo) == {}


def test_payload_without_an_agent_id_does_nothing(data_dir, git_repo, hook_env):
    """Without an id the slice cannot be identified, and guessing would misblame."""
    (git_repo / "b.py").write_text("def broken(:\n", encoding="utf-8")
    record("main", git_repo / "b.py")

    assert run_hook(SCRIPT, payload(None, git_repo), hook_env, git_repo) == {}


def test_a_worker_is_only_checked_once(data_dir, git_repo, hook_env):
    """A worker that cannot fix its slice hands back rather than spinning."""
    (git_repo / "b.py").write_text("def broken(:\n", encoding="utf-8")
    record("worker-b", git_repo / "b.py")

    first = run_hook(SCRIPT, payload("worker-b", git_repo), hook_env, git_repo)
    assert first.get("decision") == "block"
    assert run_hook(SCRIPT, payload("worker-b", git_repo), hook_env, git_repo) == {}


def test_clean_work_is_not_blocked(data_dir, git_repo, hook_env):
    (git_repo / "a.py").write_text("value = 2\n", encoding="utf-8")
    record("worker-a", git_repo / "a.py")

    assert run_hook(SCRIPT, payload("worker-a", git_repo), hook_env, git_repo) == {}


def test_a_file_outside_the_repo_is_not_checked(data_dir, git_repo, hook_env, tmp_path):
    """Paths are re-resolved long after they were recorded.

    A recorded path that has since become a symlink out of the tree would
    otherwise have the repo's tools run against the target, and their output
    echoed back into the transcript.
    """
    outside = tmp_path / "outside.py"
    outside.write_text("def broken(:\n", encoding="utf-8")
    link = git_repo / "b.py"
    link.unlink()
    link.symlink_to(outside)

    write_json(shard_path("sess", "worker-b"), {"files_touched": [str(link)]})

    assert run_hook(SCRIPT, payload("worker-b", git_repo), hook_env, git_repo) == {}


def _transcripts(tmp_path: Path, lens_in_agent: bool) -> tuple[str, str]:
    """A main-session transcript and the agent's own, in the real layout.

    Claude Code writes the subagent's transcript under `<session>/subagents/`,
    and hands `SubagentStop` both paths — `transcript_path` for the session,
    `agent_transcript_path` for the agent.
    """
    import json

    main = tmp_path / "sess.jsonl"
    main.write_text(json.dumps({"type": "user", "message": {"content": "review"}}), encoding="utf-8")

    own = tmp_path / "sess" / "subagents"
    own.mkdir(parents=True)
    agent = own / "agent-1.jsonl"
    read = "/p/references/lenses/lens-security.md" if lens_in_agent else "/repo/src/app.ts"
    agent.write_text(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": read}}]},
    }), encoding="utf-8")
    return str(main), str(agent)


def test_a_reviewer_that_read_its_lens_is_let_through(data_dir, git_repo, hook_env, tmp_path):
    """End to end, because the two halves of this were wired to different files.

    The gate read `event["transcript_path"]` — the *main session's* transcript.
    A subagent's tool calls are never in it; they are in `agent_transcript_path`.
    So the evidence half of the gate was dead, and a reviewer that did exactly
    what the catalogue asked was blocked anyway, with a message telling it to do
    the thing it had just done.
    """
    main, agent = _transcripts(tmp_path, lens_in_agent=True)
    event = payload("reviewer-1", git_repo)
    event.update(agent_type="harness:reviewer-security", transcript_path=main, agent_transcript_path=agent)

    assert run_hook(SCRIPT, event, hook_env, git_repo) == {}, (
        "blocked a reviewer for not reading a lens it read"
    )


def test_a_reviewer_that_read_no_lens_is_still_blocked(data_dir, git_repo, hook_env, tmp_path):
    """The other direction, so the test above cannot pass by the gate having been
    switched off — which is the cheaper way to make it green."""
    main, agent = _transcripts(tmp_path, lens_in_agent=False)
    event = payload("reviewer-2", git_repo)
    event.update(agent_type="harness:reviewer-security", transcript_path=main, agent_transcript_path=agent)

    response = run_hook(SCRIPT, event, hook_env, git_repo)

    assert response.get("decision") == "block"
    assert "no domain knowledge" in response.get("reason", "")
