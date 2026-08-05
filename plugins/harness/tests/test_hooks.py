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

    Only the contract prompt is silent. A worker writing over another worker is
    denied — see below — so this asserts on the absence of a *question*, not on
    an empty response in general.
    """
    push_past_the_threshold(git_repo, hook_env)

    response = run_hook(
        "pre_edit_gate.py", gate_payload(git_repo, "a.py", "worker-a"), hook_env, git_repo
    )

    assert response == {}


def test_a_worker_is_denied_a_file_another_worker_already_wrote(data_dir, git_repo, hook_env):
    """The one failure parallel work adds, and the only one it cannot report.

    Last write wins, with no error and no conflict marker, so nothing
    downstream can find it: the end-of-turn gate sees a file that was checked
    and passes it. If this deny inverts, fan-out silently drops a slice.
    """
    run_hook("post_edit_check.py", edit_payload(git_repo, "a.py", "worker-a"), hook_env, git_repo)

    response = run_hook(
        "pre_edit_gate.py", gate_payload(git_repo, "a.py", "worker-b"), hook_env, git_repo
    )

    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "worker-a" in response["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_worker_may_edit_its_own_file_while_another_worker_holds_a_different_one(
    data_dir, git_repo, hook_env
):
    """Fan-out is exactly this: two workers, two files, at the same time.

    Every other deny test uses one filename, so a gate that denies on the mere
    existence of another worker's record passes all of them. That mutation —
    `if record.get("files_touched")` instead of matching the path — kills
    parallel work on its first real run: worker B is refused its own first edit
    and told it belongs to a file it never touched.
    """
    run_hook("post_edit_check.py", edit_payload(git_repo, "a.py", "worker-a"), hook_env, git_repo)

    response = run_hook(
        "pre_edit_gate.py", gate_payload(git_repo, "b.py", "worker-b"), hook_env, git_repo
    )

    assert response == {}


def test_a_markdown_edit_is_recorded_even_though_nothing_checks_it(
    data_dir, git_repo, hook_env
):
    """`files_touched` is read by three gates; it was written by one condition.

    `post_edit_check` returned before recording when no per-file check matched
    the extension, so every `.md` edit was invisible to the scope fence, the
    end-of-turn gate and the collision deny at once. A whole session of SKILL.md
    changes went through the fence unexamined.
    """
    (git_repo / "notes.md").write_text("hello\n", encoding="utf-8")

    run_hook(
        "post_edit_check.py", edit_payload(git_repo, "notes.md", "worker-a"), hook_env, git_repo
    )

    session = load_session("sess")
    assert "notes.md" in [Path(f).name for f in session["files_touched"]]


def test_an_edit_outside_the_repository_is_not_part_of_the_change(
    data_dir, git_repo, hook_env, tmp_path
):
    """`/harness:plan` writes its contract outside the repo, every time.

    Once every edit was recorded rather than only checked ones, that put a file
    in `files_touched` that no contract could list — and the scope fence
    promptly accused the contract of not listing itself. Anything reading
    `files_touched` means files in this repository.
    """
    outside = tmp_path / "elsewhere.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    event = {
        "session_id": "sess",
        "cwd": str(git_repo),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(outside), "new_string": "value = 2\n"},
    }

    run_hook("post_edit_check.py", event, hook_env, git_repo)

    assert not load_session("sess").get("files_touched")


def test_a_worker_may_rewrite_its_own_file(data_dir, git_repo, hook_env):
    """Editing a file twice is ordinary work, not a collision."""
    run_hook("post_edit_check.py", edit_payload(git_repo, "a.py", "worker-a"), hook_env, git_repo)

    response = run_hook(
        "pre_edit_gate.py", gate_payload(git_repo, "a.py", "worker-a"), hook_env, git_repo
    )

    assert response == {}


def test_a_worker_may_edit_a_file_the_lead_already_touched(data_dir, git_repo, hook_env):
    """The lead writes the failing test before it fans out.

    Counting `main` as a colliding writer would deny the worker that owns that
    test file — turning the plan's own prescribed order into a blocked edit.
    """
    run_hook("post_edit_check.py", edit_payload(git_repo, "a.py"), hook_env, git_repo)

    response = run_hook(
        "pre_edit_gate.py", gate_payload(git_repo, "a.py", "worker-a"), hook_env, git_repo
    )

    assert response == {}


def test_the_lead_is_never_denied_a_workers_file(data_dir, git_repo, hook_env):
    """`main` resolves collisions; it does not get stopped by them."""
    run_hook("post_edit_check.py", edit_payload(git_repo, "a.py", "worker-a"), hook_env, git_repo)

    response = run_hook("pre_edit_gate.py", gate_payload(git_repo, "a.py"), hook_env, git_repo)

    assert response.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


def test_a_deletion_is_counted_by_what_it_removed(data_dir, git_repo, hook_env):
    """`lines_changed` counted `new_string` only, so tearing nine hundred lines
    out of a file cost one line.

    That number is not decoration. `session_end.outcome_for` compares it against
    the plan's `~N lines` forecast to decide whether the plan `held` or was
    `reworked`, and the roadmap entry it writes is the evidence the challenger
    cites at the start of the next plan. A large deletion recorded as one line is
    a session that overran its own forecast and was written down as having met it.
    """
    import post_edit_check

    removed = "\n".join(f"line {i}" for i in range(200))
    event = {
        "session_id": "s",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(git_repo / "a.py"),
            "old_string": removed,
            "new_string": "",
        },
    }

    assert post_edit_check._edit_size(event) == 200


def test_an_insertion_is_still_counted_by_what_it_added(data_dir, git_repo, hook_env):
    """The direction that already worked, so the fix cannot be "count the old side
    instead" — which would make every addition to an empty region cost nothing."""
    import post_edit_check

    added = "\n".join(f"line {i}" for i in range(50))
    event = {
        "session_id": "s",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(git_repo / "a.py"), "old_string": "", "new_string": added},
    }

    assert post_edit_check._edit_size(event) == 50


def test_a_multi_edit_sums_the_larger_side_of_each_hunk(data_dir, git_repo, hook_env):
    """MultiEdit carries the same shape per hunk and had the same undercount."""
    import post_edit_check

    event = {
        "session_id": "s",
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": str(git_repo / "a.py"),
            "edits": [
                {"old_string": "a\nb\nc", "new_string": "a"},
                {"old_string": "x", "new_string": "x\ny\nz\nw"},
            ],
        },
    }

    assert post_edit_check._edit_size(event) == 3 + 4


def test_a_worker_whose_id_needs_sanitising_may_still_edit_its_own_file(
    data_dir, git_repo, hook_env
):
    """Shard files are named through `state._safe`, which rewrites anything
    outside `[alnum]-_`. `_other_worker_holding` compared the raw agent id against
    those sanitised stems, so a worker with such an id never recognised its own
    shard — it found its own file, read it as another worker's, and denied itself.

    The worker then reports the file as one it could not take and its slice of the
    plan is silently never built. Every agent id seen so far happens to survive
    `_safe` unchanged, which is what makes this the kind of thing that breaks
    later and without a sound.
    """
    agent = "worker:alpha/1"  # `_safe` turns the punctuation into dashes
    run_hook("post_edit_check.py", edit_payload(git_repo, "a.py", agent), hook_env, git_repo)

    response = run_hook(
        "pre_edit_gate.py",
        {
            "session_id": "sess",
            "agent_id": agent,
            "tool_name": "Edit",
            "hook_event_name": "PreToolUse",
            "tool_input": {"file_path": str(git_repo / "a.py"), "new_string": "value = 3\n"},
        },
        hook_env,
        git_repo,
    )

    decision = (response.get("hookSpecificOutput") or {}).get("permissionDecision")
    assert decision != "deny", "the worker was denied the file it had written itself"


def test_a_worker_is_still_denied_a_file_a_sanitised_peer_holds(data_dir, git_repo, hook_env):
    """The direction the fix must not overshoot into. Matching every shard as
    "mine" would switch the collision guard off entirely, and two workers editing
    one file lose code with no error and no conflict marker."""
    run_hook("post_edit_check.py", edit_payload(git_repo, "a.py", "worker:alpha/1"), hook_env, git_repo)

    response = run_hook(
        "pre_edit_gate.py",
        {
            "session_id": "sess",
            "agent_id": "worker:beta/2",
            "tool_name": "Edit",
            "hook_event_name": "PreToolUse",
            "tool_input": {"file_path": str(git_repo / "a.py"), "new_string": "value = 4\n"},
        },
        hook_env,
        git_repo,
    )

    decision = (response.get("hookSpecificOutput") or {}).get("permissionDecision")
    assert decision == "deny", "a genuine two-worker collision went unreported"


def test_session_start_says_exactly_where_the_contract_goes(data_dir, git_repo, hook_env):
    """The path, resolved, not a template over one.

    Everything downstream is addressed by this one filename: `contract.load`
    opens it, `approved` gates the fence, `scoped_files` fills it. A plan written
    anywhere else leaves `stop_gate._out_of_scope` returning nothing, so the
    end-of-turn gate certifies every edit in the session as agreed — the loudest
    machinery in the plugin, reporting clean because it was reading an empty file
    that was never there.

    The skills used to name it `${CLAUDE_PLUGIN_DATA}/contracts/${CLAUDE_SESSION_ID}.md`
    and the second placeholder does not exist. This hook is handed the session id
    in its own payload, so it can say rather than imply.
    """
    import contract as contract_mod

    out = run_hook(
        "session_start.py",
        {"session_id": "sess-42", "cwd": str(git_repo), "source": "startup"},
        hook_env,
        git_repo,
    )
    context = out["hookSpecificOutput"]["additionalContext"]

    expected = str(contract_mod.contract_path("sess-42"))
    assert expected in context, "the model is never told which file the fence reads"
    assert "${" not in context, "an unexpanded placeholder reached the model"
    assert str(data_dir) in expected, "named a directory the hooks themselves do not use"
