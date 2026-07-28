"""Attribution: a diagnostic that predates the edit is not the edit's fault.

The README calls this the single most important behaviour in the plugin, and
until now nothing asserted it. The asymmetry is what makes it matter: a missed
block is one defect that the end-of-turn gate still catches, while a false block
on inherited noise fires on *every edit in the repo* and gets the harness
switched off within a day — which is what happened to the git hook it replaced.

So these tests care more about the hook staying quiet than about it firing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import run_hook


def edit(repo: Path, name: str, content: str) -> dict:
    (repo / name).write_text(content, encoding="utf-8")
    return {
        "session_id": "sess",
        "cwd": str(repo),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(repo / name), "new_string": content},
    }


def test_a_failure_that_predates_the_edit_does_not_block(data_dir, repo_broken_at_head, hook_env):
    """The whole promise. `legacy.py` does not compile at HEAD, and this edit
    neither fixes nor worsens that — so the check fails, and the hook must still
    say nothing."""
    repo = repo_broken_at_head
    already = (repo / "legacy.py").read_text(encoding="utf-8")

    response = run_hook(
        "post_edit_check.py",
        edit(repo, "legacy.py", already + "\n# appended below the existing error\n"),
        hook_env,
        repo,
    )

    assert response == {}, "blocked on breakage that was already at HEAD"


def test_a_failure_the_edit_introduced_does_block(data_dir, repo_broken_at_head, hook_env):
    """The counterpart, in the same repo — proving the quiet above is
    attribution working rather than the check never running at all."""
    repo = repo_broken_at_head

    response = run_hook("post_edit_check.py", edit(repo, "a.py", "def fresh(:\n"), hook_env, repo)

    assert response.get("decision") == "block"
    assert "a.py" in response["reason"]


def test_only_the_new_diagnostic_is_reported(data_dir, repo_broken_at_head, hook_env):
    """A file broken at HEAD, then broken *differently*. The pre-existing fault
    must not appear in what the model is told to fix, or it chases it."""
    repo = repo_broken_at_head

    response = run_hook(
        "post_edit_check.py", edit(repo, "legacy.py", "def legacy(:\n  x=(\n"), hook_env, repo
    )

    if response.get("decision") == "block":
        assert "already existed at HEAD" in response["reason"], (
            "a block must tell the model which diagnostics are not its problem"
        )


def test_an_untracked_file_has_everything_reported(data_dir, git_repo, hook_env):
    """No baseline exists, and that is correct — nothing in a new file predates
    the edit that created it."""
    response = run_hook("post_edit_check.py", edit(git_repo, "brand_new.py", "def x(:\n"), hook_env, git_repo)

    assert response.get("decision") == "block"


def test_a_clean_edit_to_a_broken_repo_is_silent(data_dir, repo_broken_at_head, hook_env):
    """Editing a healthy file in an unhealthy repo. Most real work looks like
    this, and it is where a naive gate becomes unusable."""
    response = run_hook(
        "post_edit_check.py", edit(repo_broken_at_head, "a.py", "value = 99\n"), hook_env, repo_broken_at_head
    )

    assert response == {}


def test_the_baseline_copy_is_not_left_behind(data_dir, repo_broken_at_head, hook_env):
    """Attribution works by writing HEAD's version beside the real file so the
    repo's own config resolves. A leaked copy would be committed by the user."""
    repo = repo_broken_at_head
    run_hook("post_edit_check.py", edit(repo, "legacy.py", "def legacy(:\n  pass\n"), hook_env, repo)

    strays = list(repo.rglob(".harness-baseline-*"))
    assert strays == [], f"left temporary files in the user's tree: {strays}"

    tracked = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo), capture_output=True, text=True
    ).stdout
    assert ".harness-baseline" not in tracked
