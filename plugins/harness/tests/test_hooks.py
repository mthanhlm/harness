"""The edit hooks, driven as processes the way Claude Code drives them.

These two scripts carry the change end to end, and both fail *silently* when
wrong — `guard()` swallows the exception and exits 0, so a broken hook is
indistinguishable from a hook that ran and found nothing. Behaviour asserted
from the outside is the only thing that tells them apart.
"""

from __future__ import annotations

from pathlib import Path

from conftest import run_hook
from state import load_session, shards_dir


def edit_payload(repo: Path, name: str, agent_id: str | None = None) -> dict:
    event = {
        "session_id": "sess",
        "cwd": str(repo),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(repo / name), "new_string": "value = 2\n"},
    }
    if agent_id is not None:
        event["agent_id"] = agent_id
    return event


def test_each_worker_gets_its_own_record(data_dir, git_repo, hook_env):
    """Without the writer plumbing every worker writes the shard named `main`.

    That failure is total and silent: each worker's own stop check then finds no
    files under its own id, reports that it wrote nothing, and every slice ships
    unverified while the hook logs look healthy.
    """
    run_hook("post_edit_check.py", edit_payload(git_repo, "a.py", "worker-a"), hook_env, git_repo)
    run_hook("post_edit_check.py", edit_payload(git_repo, "b.py", "worker-b"), hook_env, git_repo)
    run_hook("post_edit_check.py", edit_payload(git_repo, "a.py"), hook_env, git_repo)

    shards = sorted(p.name for p in shards_dir("sess").glob("*.json"))
    assert shards == ["main.json", "worker-a.json", "worker-b.json"]

    session = load_session("sess")
    assert sorted(Path(f).name for f in session["files_touched"]) == ["a.py", "b.py"]


def test_an_edit_that_breaks_a_file_is_blocked(data_dir, git_repo, hook_env):
    """Proves a per-file check really runs here, rather than the suite passing
    because the detector found no tooling and every assertion went vacuous."""
    (git_repo / "a.py").write_text("def broken(:\n", encoding="utf-8")

    response = run_hook(
        "post_edit_check.py", edit_payload(git_repo, "a.py", "worker-a"), hook_env, git_repo
    )

    assert response.get("decision") == "block"


def gate_payload(repo: Path, name: str, agent_id: str | None = None) -> dict:
    event = {
        "session_id": "sess",
        "cwd": str(repo),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(repo / name), "new_string": "x = 1\n"},
    }
    if agent_id is not None:
        event["agent_id"] = agent_id
    return event


def push_past_the_threshold(git_repo: Path, hook_env: dict) -> None:
    """Four edited files, which is past the gate's three-file trivial bound."""
    for i in range(4):
        target = git_repo / f"gen{i}.py"
        target.write_text("value = 1\n", encoding="utf-8")
        run_hook("post_edit_check.py", edit_payload(git_repo, target.name), hook_env, git_repo)


def test_the_contract_gate_asks_the_main_thread(data_dir, git_repo, hook_env):
    push_past_the_threshold(git_repo, hook_env)

    response = run_hook("pre_edit_gate.py", gate_payload(git_repo, "a.py"), hook_env, git_repo)

    decision = response.get("hookSpecificOutput", {}).get("permissionDecision")
    assert decision == "ask"


def test_the_contract_gate_stays_quiet_inside_a_worker(data_dir, git_repo, hook_env):
    """A worker cannot answer a question meant for the user.

    The inverted guard is the expensive mutation here: the user would silently
    never be asked to plan, and workers would raise prompts in the lead's
    session with no context to decide from.
    """
    push_past_the_threshold(git_repo, hook_env)

    response = run_hook(
        "pre_edit_gate.py", gate_payload(git_repo, "a.py", "worker-a"), hook_env, git_repo
    )

    assert response == {}
