"""Files changed through a shell reach the same gates as any other edit.

Before this, `sed -i` was invisible to every one of them — the scope fence, the
per-worker check, and the end-of-turn gate, which skips entirely when it
believes nothing was touched. So the interesting assertions here are about what
the fence can *see* afterwards, not about anything the hook returns.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import SCRIPTS, run_hook
from state import load_session, shard_path, session_state

import bash_watch


def bash(repo: Path, agent_id: str | None = None, phase: str = "PostToolUse") -> dict:
    event = {"session_id": "sess", "cwd": str(repo), "tool_name": "Bash",
             "hook_event_name": phase,
             "tool_input": {"command": "sed -i s/1/2/ a.py"}}
    if agent_id is not None:
        event["agent_id"] = agent_id
    return event


def before(repo: Path, env: dict, agent_id: str | None = None) -> None:
    """Sample the tree, exactly as the PreToolUse hook does."""
    run_hook("bash_watch.py", bash(repo, agent_id, "PreToolUse"), env, repo)


def touched(names_only: bool = True) -> list[str]:
    files = load_session("sess")["files_touched"]
    return sorted(Path(f).name for f in files) if names_only else files


def test_a_file_edited_through_bash_reaches_the_fence(data_dir, git_repo, hook_env):
    before(git_repo, hook_env)
    (git_repo / "a.py").write_text("value = 2\n", encoding="utf-8")

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    assert touched() == ["a.py"]


def test_a_file_the_user_changed_is_not_claimed(data_dir, git_repo, hook_env):
    """The failure that hurt most: a file the *user* was editing in their own
    window got attributed to the agent, and the end-of-turn gate then told the
    model to revert the user's uncommitted work."""
    before(git_repo, hook_env)
    (git_repo / "b.py").write_text("the user is mid-edit here\n", encoding="utf-8")
    # ...and the command itself changed nothing.

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    assert touched() == ["b.py"] or touched() == []


def test_a_file_that_was_already_dirty_is_still_noticed(data_dir, git_repo, hook_env):
    """Set-difference on paths alone missed this, and it is the state most
    sessions start in: git already lists the file, so changing it again moved
    nothing that a path comparison could see."""
    (git_repo / "a.py").write_text("dirty before the command\n", encoding="utf-8")
    before(git_repo, hook_env)
    (git_repo / "a.py").write_text("and changed again by the command\n", encoding="utf-8")

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    assert touched() == ["a.py"]


def test_without_a_pre_command_sample_nothing_is_claimed(data_dir, git_repo, hook_env):
    """Gates switched on mid-session, or a resume. Claiming the whole dirty tree
    is how the user's own work gets reverted."""
    (git_repo / "a.py").write_text("changed with no sample taken\n", encoding="utf-8")

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    assert touched() == []


def test_a_file_is_attributed_to_one_writer_only(data_dir, git_repo, hook_env):
    """Worker A's file must not land in worker B's shard, or B is checked and
    blamed for work it never did."""
    before(git_repo, hook_env, "worker-a")
    before(git_repo, hook_env, "worker-b")
    (git_repo / "a.py").write_text("value = 2\n", encoding="utf-8")

    run_hook("bash_watch.py", bash(git_repo, "worker-a"), hook_env, git_repo)
    run_hook("bash_watch.py", bash(git_repo, "worker-b"), hook_env, git_repo)

    a = json.loads(shard_path("sess", "worker-a").read_text())
    b = json.loads(shard_path("sess", "worker-b").read_text())
    assert [Path(f).name for f in a["files_touched"]] == ["a.py"]
    assert b.get("files_touched", []) == [], "worker B claimed worker A's file"


def test_a_file_already_recorded_by_an_edit_is_not_claimed_again(data_dir, git_repo, hook_env):
    before(git_repo, hook_env, "worker-a")
    (git_repo / "a.py").write_text("value = 2\n", encoding="utf-8")
    with session_state("sess", "main") as state:
        state["files_touched"] = [str(git_repo / "a.py")]

    run_hook("bash_watch.py", bash(git_repo, "worker-a"), hook_env, git_repo)

    shard = json.loads(shard_path("sess", "worker-a").read_text())
    assert shard.get("files_touched", []) == []


def test_a_new_untracked_file_counts(data_dir, git_repo, hook_env):
    """`> newfile.py` is an edit too, and it is the case a diff of tracked
    files would miss."""
    before(git_repo, hook_env)
    (git_repo / "generated.py").write_text("x = 1\n", encoding="utf-8")

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    assert touched() == ["generated.py"]


def test_a_command_that_changed_nothing_records_nothing(data_dir, git_repo, hook_env):
    before(git_repo, hook_env)

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    assert touched() == []


def test_a_wholesale_rewrite_is_capped(data_dir, git_repo, hook_env):
    """A checkout or a build touching the whole tree would bury the fence in
    paths it cannot act on, so the list is capped — but something is recorded."""
    before(git_repo, hook_env)
    for i in range(bash_watch.MAX_NEW_FILES + 5):
        (git_repo / f"gen{i}.py").write_text("x = 1\n", encoding="utf-8")

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    # Capped, but not silent: dropping the fact as well as the paths would let
    # the largest change of the session end the turn with no project check.
    assert len(touched()) == bash_watch.MAX_NEW_FILES


def test_a_non_bash_tool_is_ignored(data_dir, git_repo, hook_env):
    before(git_repo, hook_env)
    
    (git_repo / "a.py").write_text("value = 2\n", encoding="utf-8")
    event = bash(git_repo) | {"tool_name": "Read"}

    run_hook("bash_watch.py", event, hook_env, git_repo)

    assert touched() == []


def test_outside_a_git_repo_it_does_nothing(data_dir, tmp_path, hook_env):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("x = 1\n", encoding="utf-8")

    assert run_hook("bash_watch.py", bash(plain), hook_env, plain) == {}


def test_a_rename_records_the_destination(data_dir, git_repo, hook_env):
    before(git_repo, hook_env)
    subprocess.run(["git", "mv", "a.py", "renamed.py"], cwd=str(git_repo), check=True, capture_output=True)

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    assert "renamed.py" in touched()


def test_a_command_that_failed_still_had_its_writes_recorded(data_dir, git_repo, hook_env):
    """`PostToolUse` fires only on success, and a failing command writes anyway.

    A build that emits `dist/` and then fails its own check, a `sed -i` that dies
    on the third file having rewritten the first two. Without the failure event
    nothing records them and nothing is left behind either: the parked
    pre-command sample is overwritten by the next command, so the files enter no
    gate at all and the end-of-turn check reports over a tree it never saw.
    """
    before(git_repo, hook_env)
    (git_repo / "a.py").write_text("value = 2\n", encoding="utf-8")

    event = bash(git_repo, phase="PostToolUseFailure")
    event["error"] = "Exit code 1\nsed: can't read c.py: No such file or directory"
    run_hook("bash_watch.py", event, hook_env, git_repo)

    assert "a.py" in touched(), "a failing command's writes reached no gate"


def test_a_powershell_command_is_watched_too(data_dir, git_repo, hook_env):
    """On Windows without Git Bash, Claude Code enables the PowerShell tool and
    does not register Bash at all, so every shell command arrives under the other
    name. A guard naming only `Bash` drops all of them while the matcher in
    `hooks.json` is what looks like the wiring."""
    pre = bash(git_repo, phase="PreToolUse")
    pre["tool_name"] = "PowerShell"
    pre["tool_input"] = {"command": "Set-Content a.py 'value = 2'"}
    run_hook("bash_watch.py", pre, hook_env, git_repo)

    (git_repo / "a.py").write_text("value = 2\n", encoding="utf-8")
    post = dict(pre, hook_event_name="PostToolUse")
    run_hook("bash_watch.py", post, hook_env, git_repo)

    assert "a.py" in touched(), "a PowerShell edit reached no gate"
