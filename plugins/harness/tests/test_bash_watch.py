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
from state import load_session, save_session, shard_path, session_state

import bash_watch


def baseline(repo: Path) -> None:
    """What session start does: record what was already dirty."""
    save_session(
        {
            "session_id": "sess",
            "repo_root": str(repo),
            "files_touched": [],
            "lines_changed": 0,
            "bash_baseline": sorted(bash_watch._porcelain(repo) or []),
        },
        reset=True,
    )


def bash(repo: Path, agent_id: str | None = None) -> dict:
    event = {"session_id": "sess", "cwd": str(repo), "tool_name": "Bash",
             "tool_input": {"command": "sed -i s/1/2/ a.py"}}
    if agent_id is not None:
        event["agent_id"] = agent_id
    return event


def touched(names_only: bool = True) -> list[str]:
    files = load_session("sess")["files_touched"]
    return sorted(Path(f).name for f in files) if names_only else files


def test_a_file_edited_through_bash_reaches_the_fence(data_dir, git_repo, hook_env):
    baseline(git_repo)
    (git_repo / "a.py").write_text("value = 2\n", encoding="utf-8")

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    assert touched() == ["a.py"]


def test_what_was_already_dirty_is_not_claimed(data_dir, git_repo, hook_env):
    """Work the user did before the session started is not this turn's doing,
    and putting it in the fence would flag it as unagreed scope creep."""
    (git_repo / "a.py").write_text("edited before the session\n", encoding="utf-8")
    baseline(git_repo)

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    assert touched() == []


def test_a_file_is_attributed_to_one_writer_only(data_dir, git_repo, hook_env):
    """Worker A's file must not land in worker B's shard, or B is checked and
    blamed for work it never did."""
    baseline(git_repo)
    (git_repo / "a.py").write_text("value = 2\n", encoding="utf-8")

    run_hook("bash_watch.py", bash(git_repo, "worker-a"), hook_env, git_repo)
    run_hook("bash_watch.py", bash(git_repo, "worker-b"), hook_env, git_repo)

    a = json.loads(shard_path("sess", "worker-a").read_text())
    assert [Path(f).name for f in a["files_touched"]] == ["a.py"]
    assert not shard_path("sess", "worker-b").is_file()


def test_a_file_already_recorded_by_an_edit_is_not_claimed_again(data_dir, git_repo, hook_env):
    baseline(git_repo)
    (git_repo / "a.py").write_text("value = 2\n", encoding="utf-8")
    with session_state("sess", "main") as state:
        state["files_touched"] = [str(git_repo / "a.py")]

    run_hook("bash_watch.py", bash(git_repo, "worker-a"), hook_env, git_repo)

    assert not shard_path("sess", "worker-a").is_file()


def test_a_new_untracked_file_counts(data_dir, git_repo, hook_env):
    """`> newfile.py` is an edit too, and it is the case a diff of tracked
    files would miss."""
    baseline(git_repo)
    (git_repo / "generated.py").write_text("x = 1\n", encoding="utf-8")

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    assert touched() == ["generated.py"]


def test_a_command_that_changed_nothing_records_nothing(data_dir, git_repo, hook_env):
    baseline(git_repo)

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    assert touched() == []


def test_a_wholesale_rewrite_is_not_recorded(data_dir, git_repo, hook_env):
    """A checkout or a build touching the whole tree is not an edit, and
    recording it would bury the fence in paths it cannot act on."""
    baseline(git_repo)
    for i in range(bash_watch.MAX_NEW_FILES + 5):
        (git_repo / f"gen{i}.py").write_text("x = 1\n", encoding="utf-8")

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    assert touched() == []


def test_a_non_bash_tool_is_ignored(data_dir, git_repo, hook_env):
    baseline(git_repo)
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
    baseline(git_repo)
    subprocess.run(["git", "mv", "a.py", "renamed.py"], cwd=str(git_repo), check=True, capture_output=True)

    run_hook("bash_watch.py", bash(git_repo), hook_env, git_repo)

    assert "renamed.py" in touched()
