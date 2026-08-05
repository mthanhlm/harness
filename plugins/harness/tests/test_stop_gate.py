"""The end-of-turn gate, driven as the process Claude Code drives.

Both behaviours here were found in a real `gate.log` rather than by reading the
code, and both fail the same way: the gate reports a good outcome having done
nothing. That is worse than a gate that is switched off, because a switched-off
gate does not tell you the project is fine.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from conftest import run_hook


def plain_repo(tmp_path: Path) -> Path:
    """A repo with no manifest of any kind, so no project check exists.

    `git_repo` cannot be used here: it writes a `pytest.ini`, which is exactly
    what gives a repo a project-scoped check. The point of these tests is the
    repo that has none — which is most repos, and was every repo the day this
    was found.
    """
    root = tmp_path / "plain"
    root.mkdir()
    (root / "a.json").write_text('{"a": 1}\n', encoding="utf-8")
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "base"],
    ):
        subprocess.run(argv, cwd=str(root), check=True, capture_output=True)
    return root


def edit_json(repo: Path, name: str) -> dict:
    """A JSON edit: file-scoped checks exist for it, project-scoped ones do not."""
    return {
        "session_id": "sess",
        "cwd": str(repo),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(repo / name), "new_string": '{"a": 2}\n'},
    }


def stop_lines(data_dir: Path) -> list[dict]:
    log = data_dir / "gate.log"
    if not log.exists():
        return []
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [e for e in entries if e.get("hook") == "Stop"]


def test_a_gate_with_no_checks_says_nothing_to_verify_not_passed(data_dir, hook_env, tmp_path):
    """`passed` and `nothing ran` must not be the same word.

    `_run_until_failure` returns four empty lists when the profile has no
    project checks, so `blocking` is empty and the gate takes the success
    branch. It then reports `passed` with `ok: []` — indistinguishable from a
    turn where the tests genuinely ran and were green.

    This is the defect the plugin exists to prevent, produced by the plugin: it
    teaches you to trust a turn nothing verified.
    """
    repo = plain_repo(tmp_path)
    run_hook("post_edit_check.py", edit_json(repo, "a.json"), hook_env, repo)

    run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    outcomes = [e["outcome"] for e in stop_lines(data_dir)]
    assert "nothing to verify" in outcomes, outcomes
    assert "passed" not in outcomes, "a gate that ran no checks must not report passed"


def test_a_shell_edit_makes_the_gate_run_again(data_dir, hook_env, tmp_path):
    """The skip key has to move when a shell command changes a file.

    `post_edit_check` bumps `lines_changed`; `bash_watch` records
    `files_touched` and never touches it. So after one Stop has run, a session
    that goes on editing through the shell keeps the same line count forever and
    every later Stop takes the cheap skip. In the log this was 92 skips against
    46 real runs, with the file count climbing 21 -> 50 the whole time.
    """
    repo = plain_repo(tmp_path)
    run_hook("post_edit_check.py", edit_json(repo, "a.json"), hook_env, repo)
    run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    bash = {
        "session_id": "sess",
        "cwd": str(repo),
        "tool_name": "Bash",
        "hook_event_name": "PreToolUse",
        "tool_input": {"command": "true"},
    }
    run_hook("bash_watch.py", bash, hook_env, repo)
    (repo / "b.json").write_text('{"b": 1}\n', encoding="utf-8")
    run_hook("bash_watch.py", {**bash, "hook_event_name": "PostToolUse"}, hook_env, repo)

    run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    last = stop_lines(data_dir)[-1]["outcome"]
    assert not last.startswith("skipped"), f"a new file must re-arm the gate, got {last!r}"


def test_editing_the_same_file_again_makes_the_gate_run_again(data_dir, hook_env, tmp_path):
    """The other half of the key, which the shell-edit test does not pin.

    Keying only on the file count passes every other test here — and a normal
    implement turn edits files it has already touched, over and over. The count
    never moves, so every Stop after the first takes the cheap skip: the exact
    92-skips-to-46-runs bug this was written to fix, reintroduced on the other
    axis and just as invisible.
    """
    repo = plain_repo(tmp_path)
    run_hook("post_edit_check.py", edit_json(repo, "a.json"), hook_env, repo)
    run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    run_hook("post_edit_check.py", edit_json(repo, "a.json"), hook_env, repo)
    run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    last = stop_lines(data_dir)[-1]["outcome"]
    assert not last.startswith("skipped"), f"more lines in the same file must re-arm, got {last!r}"


def repo_with_a_real_suite(tmp_path: Path) -> Path:
    """A repo whose pytest suite genuinely passes at HEAD.

    Neither existing fixture can prove a fail-open. `plain_repo` has no project
    check to skip, and `git_repo` writes a `pytest.ini` over a tree with nothing
    to collect — pytest exits 5 for that, which fails at HEAD too and is
    correctly reported as inherited breakage rather than a pass. Showing that
    the gate ends a turn over a red suite needs a suite that is green first.
    """
    root = tmp_path / "suite"
    (root / "tests").mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    # A module and a test that imports it, both committed. The pair is what lets
    # a revert of one of them break the other — a suite of self-contained
    # asserts cannot be broken by taking a change away.
    (root / "tests" / "lib.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (root / "tests" / "test_ok.py").write_text(
        "from lib import one\n\n\ndef test_ok():\n    assert one() == 1\n", encoding="utf-8"
    )
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "base"],
    ):
        subprocess.run(argv, cwd=str(root), check=True, capture_output=True)
    return root


def test_a_shell_edit_to_an_already_touched_file_makes_the_gate_run_again(
    data_dir, hook_env, tmp_path
):
    """The turn must not end green when a shell command left the suite red.

    `test_a_shell_edit_makes_the_gate_run_again` pins the case where the shell
    creates a *new* file, which moves the file count. The common case is the
    other one: a model iterating on a file it already edited, through `sed -i`,
    a redirect or a formatter. Neither half of the key moves for that.
    `bash_watch` never writes `lines_changed` — `post_edit_check` is its only
    writer — and it drops any path already in `files_touched`, so the shell edit
    is invisible to both halves at once.

    Reproduced against the real hooks before this test was written: the gate
    logged `skipped: already passed at this file and line count` while pytest
    was red. That is the plugin's central promise inverted — not a gate that
    failed to catch something, but a gate that reported it had checked.
    """
    repo = repo_with_a_real_suite(tmp_path)
    target = repo / "tests" / "test_ok.py"
    edit = {
        "session_id": "sess",
        "cwd": str(repo),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target), "new_string": "def test_two():\n    assert 2 == 2\n"},
    }

    target.write_text(
        "def test_ok():\n    assert 1 == 1\n\n\ndef test_two():\n    assert 2 == 2\n",
        encoding="utf-8",
    )
    run_hook("post_edit_check.py", edit, hook_env, repo)
    run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)
    assert stop_lines(data_dir)[-1]["outcome"] == "passed", "the suite must be green first"

    bash = {
        "session_id": "sess",
        "cwd": str(repo),
        "tool_name": "Bash",
        "hook_event_name": "PreToolUse",
        "tool_input": {"command": "sed -i s/1/2/ tests/test_ok.py"},
    }
    run_hook("bash_watch.py", bash, hook_env, repo)
    # Deliberately a different length from the version above, and not just a
    # changed digit. CPython validates cached bytecode on (mtime, size) at
    # one-second resolution, so a same-size rewrite inside the same second is
    # served from `__pycache__` and the broken file passes — which made an
    # earlier draft of this test green against an unfixed gate.
    target.write_text(
        "def test_ok():\n    assert 1 == 2, 'broken through the shell'\n\n\ndef test_two():\n    assert 2 == 3\n",
        encoding="utf-8",
    )
    run_hook("bash_watch.py", {**bash, "hook_event_name": "PostToolUse"}, hook_env, repo)

    verdict = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    last = stop_lines(data_dir)[-1]["outcome"]
    assert not last.startswith("skipped"), f"a shell edit must re-arm the gate, got {last!r}"
    assert verdict.get("decision") == "block", f"the suite is red; the turn must not end: {verdict}"


def test_a_shell_command_that_reverts_a_file_makes_the_gate_run_again(
    data_dir, hook_env, tmp_path
):
    """A command can break the suite by taking a change away, not making one.

    `git checkout -- f`, `git stash` and `git clean` remove the path from
    `git status` entirely, so the post-command sample no longer holds it and a
    comparison over that sample alone sees nothing whatsoever. The test above
    pins a shell edit that leaves the file dirty; this is the same fail-open
    reached from the opposite direction, and it was live after that one was
    fixed — `git checkout` is a command models run constantly.

    Reverting half of a pair is what makes it bite. `lib.py` goes back to HEAD
    while the test calling its new function stays behind, so a suite that was
    green two hooks ago no longer imports. None of the three counters move: the
    path was already in `files_touched`, `lines_changed` has only the edit hook
    for a writer, and `shell_changes` counted zero until `_undone` existed.
    """
    repo = repo_with_a_real_suite(tmp_path)
    lib, target = repo / "tests" / "lib.py", repo / "tests" / "test_ok.py"
    edit = {"session_id": "sess", "cwd": str(repo), "tool_name": "Edit"}

    lib.write_text("def one():\n    return 1\n\n\ndef two():\n    return 2\n", encoding="utf-8")
    run_hook(
        "post_edit_check.py",
        {**edit, "tool_input": {"file_path": str(lib), "new_string": "def two():\n    return 2\n"}},
        hook_env,
        repo,
    )
    target.write_text(
        "from lib import one, two\n\n\ndef test_ok():\n    assert one() == 1\n\n\n"
        "def test_two():\n    assert two() == 2\n",
        encoding="utf-8",
    )
    run_hook(
        "post_edit_check.py",
        {**edit, "tool_input": {"file_path": str(target), "new_string": "def test_two():\n    assert two() == 2\n"}},
        hook_env,
        repo,
    )
    run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)
    assert stop_lines(data_dir)[-1]["outcome"] == "passed", "the suite must be green first"

    bash = {
        "session_id": "sess",
        "cwd": str(repo),
        "tool_name": "Bash",
        "hook_event_name": "PreToolUse",
        "tool_input": {"command": "git checkout -- tests/lib.py"},
    }
    run_hook("bash_watch.py", bash, hook_env, repo)
    subprocess.run(
        ["git", "checkout", "--", "tests/lib.py"], cwd=str(repo), check=True, capture_output=True
    )
    run_hook("bash_watch.py", {**bash, "hook_event_name": "PostToolUse"}, hook_env, repo)

    verdict = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    last = stop_lines(data_dir)[-1]["outcome"]
    assert not last.startswith("skipped"), f"a reverted file must re-arm the gate, got {last!r}"
    assert verdict.get("decision") == "block", f"the suite is red; the turn must not end: {verdict}"


def test_reverting_a_file_the_session_never_touched_does_not_claim_it(
    data_dir, hook_env, tmp_path
):
    """The counter moves; the ledger does not.

    The obvious way to notice a reverted path is to compare over both samples
    at once, and it ships a worse bug than the one it fixes: the path lands in
    `files_touched`, the scope fence reports it as an unagreed change, and the
    gate ends the turn demanding a revert of a file that already matches HEAD.
    Nothing the model can do satisfies that.

    Here the dirty file is the user's, in their own editor, and the command is
    a `git checkout -- .` the model ran for its own reasons.
    """
    from state import load_session

    repo = plain_repo(tmp_path)
    (repo / "a.json").write_text('{"a": 99}\n', encoding="utf-8")  # the user's work, not ours

    bash = {
        "session_id": "sess",
        "cwd": str(repo),
        "tool_name": "Bash",
        "hook_event_name": "PreToolUse",
        "tool_input": {"command": "git checkout -- ."},
    }
    run_hook("bash_watch.py", bash, hook_env, repo)
    subprocess.run(["git", "checkout", "--", "."], cwd=str(repo), check=True, capture_output=True)
    run_hook("bash_watch.py", {**bash, "hook_event_name": "PostToolUse"}, hook_env, repo)

    session = load_session("sess")
    assert session["shell_changes"] == 1, f"the gate must notice the revert: {session}"
    assert session["files_touched"] == [], (
        f"a path that is clean again must never be claimed as touched: {session['files_touched']}"
    )


def test_a_pending_contract_is_said_out_loud(data_dir, hook_env, tmp_path):
    """A plan nobody approved is worse than no plan at all.

    `_out_of_scope` returns nothing for an unapproved contract, so the fence is
    silently inert while the session looks planned. Three of six real contracts
    sat pending with edits landing against them. The note is the only signal,
    and without a test it can be deleted with the suite still green.
    """
    import contract as contract_mod

    repo = plain_repo(tmp_path)
    path = contract_mod.contract_path("sess")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Plan: x\n\nstatus: pending\nverdict: patch\n", encoding="utf-8")
    run_hook("post_edit_check.py", edit_json(repo, "a.json"), hook_env, repo)

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert "scope fence is not active" in response.get("systemMessage", "")


def approved_contract(session_id: str, scoped: list[str]) -> None:
    """Write an approved contract scoping exactly the given paths."""
    import contract as contract_mod

    body = "\n".join(f"- `{entry}`" for entry in scoped)
    path = contract_mod.contract_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# Plan: x\n\nstatus: approved\nverdict: patch\n\n## Scope\n{body}\n\n## Notes\n",
        encoding="utf-8",
    )


def start_session(
    repo: Path, hook_env: dict, session_id: str = "sess", source: str = "startup"
) -> None:
    """Run the real SessionStart hook, which is what anchors `base_commit`."""
    run_hook(
        "session_start.py",
        {"session_id": session_id, "cwd": str(repo), "source": source},
        hook_env,
        repo,
    )


def head_of(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_the_scope_anchor_survives_a_compaction(data_dir, hook_env, tmp_path):
    """A compaction must not move the fence's fixed point.

    This is the case that decides whether the anchor is worth having, because a
    long session is compacted several times and each one runs SessionStart
    again. Re-anchoring there would make every out-of-scope change made *before*
    the compaction match the new anchor exactly, and the fence would forgive all
    of it — turning compaction into a laundry for scope creep, triggered by
    nothing the model has to choose to do.

    `/clear` is the opposite case and must re-anchor: the user asked for a clean
    slate, and the same branch empties `files_touched`. An anchor left behind
    there would be judging a fresh start against the previous session's tree.

    Written after a mutation showed the whole distinction was unpinned: moving
    the assignment out of the `fresh` branch passed 255 of 256 tests, and the
    one failure was the version-bump check, which fires on any edit at all.
    """
    from state import load_session

    def anchor() -> str:
        return str(load_session("sess").get("base_commit") or "")

    repo = plain_repo(tmp_path)
    start_session(repo, hook_env)
    anchored = anchor()
    assert anchored == head_of(repo), "a fresh start anchors at the commit it opened on"

    (repo / "a.json").write_text('{"a": 2}\n', encoding="utf-8")
    for argv in (["git", "add", "-A"], ["git", "commit", "-qm", "work lands"]):
        subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)
    moved = head_of(repo)
    assert moved != anchored, "precondition: HEAD must have left the anchor behind"

    for source in ("compact", "resume"):
        start_session(repo, hook_env, source=source)
        assert anchor() == anchored, (
            f"{source} must keep the anchor the session opened with, not re-take it"
        )

    start_session(repo, hook_env, source="clear")
    assert anchor() == moved, "clear is a clean slate, and re-anchors with the counters"


def test_a_stray_that_was_put_back_is_not_reported(data_dir, hook_env, tmp_path):
    """A file edited and restored is not a change, and must not be demanded back.

    `files_touched` is append-only, so the path stays on the ledger after the
    edit is undone and the fence keeps naming it for the rest of the session —
    an instruction with nothing behind it, since the diff is already clean.
    Found by this plugin reporting it on its own repo: a reviewer subagent
    mutation-tested `conftest.py` through the Write tool, restored it byte for
    byte, and the gate blocked the turn over a file identical to HEAD.
    """
    repo = plain_repo(tmp_path)
    start_session(repo, hook_env)
    approved_contract("sess", ["utils.ts"])
    stray = repo / "a.json"
    original = stray.read_text(encoding="utf-8")

    stray.write_text('{"a": 2}\n', encoding="utf-8")
    run_hook("post_edit_check.py", edit_json(repo, "a.json"), hook_env, repo)
    stray.write_text(original, encoding="utf-8")

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert "a.json" not in json.dumps(response), (
        f"a file identical to what the session started from is not a stray: {response}"
    )


def test_a_stray_committed_inside_the_turn_is_still_reported(data_dir, hook_env, tmp_path):
    """The reason the comparison is anchored, and not made against HEAD.

    Committing an unagreed change is not undoing it. But it moves HEAD onto the
    change, so a fence that asks "does this differ from HEAD" compares the edit
    to itself, finds nothing, and waves through the one path that got past
    review by being buried in history rather than reverted. Comparing against
    the commit the session opened at is what makes committing no escape.
    """
    repo = plain_repo(tmp_path)
    start_session(repo, hook_env)
    approved_contract("sess", ["utils.ts"])

    (repo / "a.json").write_text('{"a": 2}\n', encoding="utf-8")
    run_hook("post_edit_check.py", edit_json(repo, "a.json"), hook_env, repo)
    for argv in (["git", "add", "-A"], ["git", "commit", "-qm", "slip it in"]):
        subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") == "block", f"committing must not clear the fence: {response}"
    assert "a.json" in response["hookSpecificOutput"]["additionalContext"]


def test_a_new_unagreed_file_git_never_tracked_is_still_reported(data_dir, hook_env, tmp_path):
    """An untracked file compares equal to every commit, and is pure scope creep.

    `git diff <base> -- <path>` says nothing at all about a path git does not
    track, so it exits 0 and a new unagreed file reads as "unchanged since the
    session began". That is the opposite of the truth, and it would exempt the
    single most reportable kind of change. Only the `ls-files` probe separates
    "put back" from "git never knew this".
    """
    repo = plain_repo(tmp_path)
    start_session(repo, hook_env)
    approved_contract("sess", ["utils.ts"])
    stray = repo / "brand-new.ts"
    stray.write_text("export {};\n", encoding="utf-8")
    run_hook(
        "post_edit_check.py",
        {
            "session_id": "sess",
            "cwd": str(repo),
            "tool_name": "Edit",
            "tool_input": {"file_path": str(stray), "new_string": "export {};\n"},
        },
        hook_env,
        repo,
    )

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") == "block", f"a new unagreed file is a stray: {response}"
    assert "brand-new.ts" in response["hookSpecificOutput"]["additionalContext"]


def test_a_stray_sharing_a_suffix_with_a_scoped_path_is_still_out_of_scope(data_dir, hook_env, tmp_path):
    """A bare `endswith` treats any file whose name ends in a scoped filename as scoped.

    A contract scoping `utils.ts` must not wave through `src/lib/other-utils.ts`
    just because the string `utils.ts` happens to be a suffix of it — that is a
    different file in a different directory, and the scope fence exists to catch
    exactly this kind of change nobody agreed to.
    """
    repo = plain_repo(tmp_path)
    approved_contract("sess", ["utils.ts"])
    stray = repo / "src" / "lib" / "other-utils.ts"
    stray.parent.mkdir(parents=True)
    stray.write_text("export {};\n", encoding="utf-8")
    run_hook(
        "post_edit_check.py",
        {
            "session_id": "sess",
            "cwd": str(repo),
            "tool_name": "Edit",
            "tool_input": {"file_path": str(stray), "new_string": "export {};\n"},
        },
        hook_env,
        repo,
    )

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") == "block"
    assert "other-utils.ts" in response["hookSpecificOutput"]["additionalContext"]


def test_a_dotfile_the_contract_scoped_is_not_accused_of_being_a_stray(data_dir, hook_env, tmp_path):
    """Normalising scope entries with `lstrip("./")` silently mangles dotfiles.

    `lstrip` takes a *set* of characters, so `.github/workflows/ci.yml` became
    `github/workflows/ci.yml` and `.env.example` became `env.example`. Under the
    old suffix match the mangling cancelled out — the touched path ends with the
    mangled entry either way — so it sat there harmless and invisible. Against
    an exact comparison it inverts the gate: the contract can no longer scope any
    dotfile, and the turn is blocked over a file the plan explicitly named, with
    no way for the user to satisfy it.

    `.github/`, `.env.example` and `.claude/settings.json` are all things this
    plugin's own users write constantly, so this is the failure that would have
    been hit first.
    """
    repo = plain_repo(tmp_path)
    approved_contract("sess", [".github/workflows/ci.yml"])
    agreed = repo / ".github" / "workflows" / "ci.yml"
    agreed.parent.mkdir(parents=True)
    agreed.write_text("name: ci\n", encoding="utf-8")
    run_hook(
        "post_edit_check.py",
        {
            "session_id": "sess",
            "cwd": str(repo),
            "tool_name": "Edit",
            "tool_input": {"file_path": str(agreed), "new_string": "name: ci\n"},
        },
        hook_env,
        repo,
    )

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") != "block", (
        "the contract named this file; the fence must not accuse it"
    )


def test_a_path_outside_the_repo_root_is_matched_only_at_a_separator(data_dir, hook_env, tmp_path):
    """The fallback branch, which nothing else reaches.

    A touched path that is not under the repo root cannot be made relative to
    it, so it falls back to an anchored suffix match. That branch is where the
    original unanchored bug lived, and without a test it can be reverted to
    `endswith(entry)` — or removed entirely by returning True — with the suite
    still green. Both mutations restore a silent fail-open.
    """
    import stop_gate

    root = Path("/repo")
    approved_contract("outside", ["utils.ts"])

    assert stop_gate._out_of_scope("outside", ["/elsewhere/src/other-utils.ts"], root) == [
        "/elsewhere/src/other-utils.ts"
    ]
    assert stop_gate._out_of_scope("outside", ["/elsewhere/src/utils.ts"], root) == []


def test_a_monorepo_package_json_at_the_wrong_path_is_still_a_stray(data_dir, hook_env, tmp_path):
    """The same suffix bug, in the shape it was actually found: a common filename.

    A contract scoping the repo-root `package.json` must not accept
    `apps/web/package.json` as a match — the two are different files, and
    `package.json` is common enough that this is the realistic version of the
    defect, not a contrived one.
    """
    repo = plain_repo(tmp_path)
    approved_contract("sess", ["package.json"])
    stray = repo / "apps" / "web" / "package.json"
    stray.parent.mkdir(parents=True)
    stray.write_text("{}\n", encoding="utf-8")
    run_hook(
        "post_edit_check.py",
        {
            "session_id": "sess",
            "cwd": str(repo),
            "tool_name": "Edit",
            "tool_input": {"file_path": str(stray), "new_string": "{}\n"},
        },
        hook_env,
        repo,
    )

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") == "block"
    assert "apps/web/package.json" in response["hookSpecificOutput"]["additionalContext"]


def test_a_genuinely_scoped_file_passes(data_dir, hook_env, tmp_path):
    """The fence must not fire on the files the plan actually named.

    A gate that blocks everything is as useless as one that blocks nothing —
    this pins that an exact match on a repo-relative path is accepted, so a
    tighter comparison than a suffix match does not become a false positive.
    """
    repo = plain_repo(tmp_path)
    approved_contract("sess", ["src/auth/session.ts"])
    scoped_file = repo / "src" / "auth" / "session.ts"
    scoped_file.parent.mkdir(parents=True)
    scoped_file.write_text("export {};\n", encoding="utf-8")
    run_hook(
        "post_edit_check.py",
        {
            "session_id": "sess",
            "cwd": str(repo),
            "tool_name": "Edit",
            "tool_input": {"file_path": str(scoped_file), "new_string": "export {};\n"},
        },
        hook_env,
        repo,
    )

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") != "block"


def test_an_unapproved_contract_still_blocks_nothing(data_dir, hook_env, tmp_path):
    """The scope fence must stay inert until the plan is approved.

    `_out_of_scope` returns early for a contract that is not approved. Tightening
    the comparison from a suffix match to an exact one must not accidentally
    start blocking a pending plan — a plan written and never approved is a
    strictly weaker state than no plan, and must not start enforcing scope.
    """
    import contract as contract_mod

    repo = plain_repo(tmp_path)
    path = contract_mod.contract_path("sess")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Plan: x\n\nstatus: pending\nverdict: patch\n\n## Scope\n- `utils.ts`\n\n## Notes\n",
        encoding="utf-8",
    )
    stray = repo / "src" / "lib" / "other-utils.ts"
    stray.parent.mkdir(parents=True)
    stray.write_text("export {};\n", encoding="utf-8")
    run_hook(
        "post_edit_check.py",
        {
            "session_id": "sess",
            "cwd": str(repo),
            "tool_name": "Edit",
            "tool_input": {"file_path": str(stray), "new_string": "export {};\n"},
        },
        hook_env,
        repo,
    )

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") != "block"


def failing_python_repo(tmp_path: Path) -> Path:
    """A repo whose project check genuinely fails, and whose HEAD is clean.

    `plain_repo` has no project check at all, so it cannot tell "the gate ran and
    found the failure" from "the gate skipped". Both of the tests below turn on
    exactly that difference, so they need a check that has something to say.
    """
    root = tmp_path / "pyrepo"
    (root / "tests").mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (root / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (root / "a.json").write_text('{"a": 1}\n', encoding="utf-8")
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "base"],
    ):
        subprocess.run(argv, cwd=str(root), check=True, capture_output=True)
    return root


def break_the_suite(repo: Path) -> None:
    (repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert False\n", encoding="utf-8")


def test_a_scope_block_does_not_disarm_the_next_turns_checks(data_dir, hook_env, tmp_path):
    """A block on scope must not be recorded as though the checks had run.

    The scope branch returns early — before any project check — but still wrote
    `heavy_ran_at`. The skip test at the top of the next Stop is
    `heavy_ran_at == ran_at and not heavy_blocked`, and a scope block sets no
    `heavy_blocked`, so the next Stop takes the cheap skip and logs "already
    passed at this file and line count".

    Nothing passed. Nothing ran. The project is broken, the gate says it is
    fine, and the only cost of getting there is being blocked once on scope —
    which is a thing the harness does to itself, routinely.
    """
    repo = failing_python_repo(tmp_path)
    approved_contract("sess", ["a.json"])
    break_the_suite(repo)

    stray = repo / "stray.json"
    stray.write_text("{}\n", encoding="utf-8")
    for name in ("a.json", "stray.json"):
        run_hook("post_edit_check.py", edit_json(repo, name), hook_env, repo)

    first = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)
    assert first.get("decision") == "block", "expected the scope fence to fire first"
    assert first.get("reason") == "changes outside the agreed scope"

    second = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    outcome = stop_lines(data_dir)[-1]["outcome"]
    assert not outcome.startswith("skipped"), (
        f"a scope block ran no project check, so the next Stop must run them, got {outcome!r}"
    )
    assert second.get("decision") == "block", "the suite is red; the gate must say so"


def test_the_block_counter_comes_down_once_the_project_is_green(data_dir, hook_env, tmp_path):
    """`consecutive_stop_blocks` is meant to count *consecutive* failures.

    At the ceiling the gate returns before running anything, so it can never
    reach the branch that resets the counter to zero. The counter therefore only
    ever goes up: once a session has been blocked three times, the gate is dead
    for the rest of that session — including for breakage introduced afterwards —
    and every later turn is told "still failing after 3 attempts" whether or not
    anything is failing.

    Only a fresh SessionStart clears it. Resume and compact do not.
    """
    import state

    repo = failing_python_repo(tmp_path)
    run_hook("post_edit_check.py", edit_json(repo, "a.json"), hook_env, repo)

    with state.session_state("sess") as session:
        session["consecutive_stop_blocks"] = 3

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert "still failing" not in response.get("systemMessage", ""), (
        "the project is green; the gate must not claim a failure it never looked for"
    )
    outcomes = [e["outcome"] for e in stop_lines(data_dir)]
    assert "passed" in outcomes, outcomes
    assert int(state.load_session("sess").get("consecutive_stop_blocks") or 0) == 0


def test_the_ceiling_holds_even_when_every_failure_is_a_different_one(data_dir, hook_env, tmp_path):
    """Three blocks is three blocks, whether or not the failure keeps changing.

    The repeat suppression is keyed on the failure's signature, so a model that
    breaks something new each turn is never "the same failure twice" and slips
    past it. Without a ceiling that ignores the signature, the gate blocks 4, 5,
    6 times — which is the stuck loop at Opus prices that the whole mechanism
    exists to bound. Moving the ceiling below the checks (so the counter can
    reset) is what made this reachable, so it is pinned here rather than assumed.
    """
    import state

    repo = failing_python_repo(tmp_path)
    run_hook("post_edit_check.py", edit_json(repo, "a.json"), hook_env, repo)
    break_the_suite(repo)

    with state.session_state("sess") as session:
        session["consecutive_stop_blocks"] = 3
        session["heavy_blocked"] = {"some-older-signature": "pytest"}

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") != "block", (
        "three blocks is the ceiling; a new signature must not buy a fourth"
    )
    assert "still failing" in response.get("systemMessage", "")


def test_a_session_written_by_the_previous_version_does_not_kill_the_gate(data_dir, hook_env, tmp_path):
    """`scope_reported` held a boolean until 0.7.0, and `set(True)` raises.

    The raise lands inside `guard()`, which exists so a hook bug can never break
    a session — so the hook exits 0 with no output and the turn ends looking
    verified. Nothing rewrites the key, so it recurs on every Stop for that
    session id: one upgrade, and every resumed session has a dead gate.

    Four session files on the machine this shipped from carried the old value,
    which is what makes this a live upgrade path rather than a hypothetical.
    """
    import state

    repo = plain_repo(tmp_path)
    approved_contract("sess", ["a.json"])
    stray = repo / "stray.json"
    stray.write_text("{}\n", encoding="utf-8")
    run_hook("post_edit_check.py", edit_json(repo, "stray.json"), hook_env, repo)

    session = state.load_session("sess")
    session["scope_reported"] = True  # exactly what 0.6.0 wrote
    state.save_session(session, reset=False)

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") == "block", (
        "a state file from the previous version must not disarm the gate"
    )


def test_a_fresh_start_forgets_which_strays_were_already_reported(data_dir, hook_env, tmp_path):
    """Which strays have been reported belongs to a contract, not to a session id.

    Session ids are reused — `session_start.py` excludes `startup` from the
    carry-over for exactly that reason. So a `scope_reported` list that survives
    a fresh start exempts a path from the fence under a completely different
    plan: the same never-cleared latch the boolean version was, one field over,
    and invisible in the same way.
    """
    repo = plain_repo(tmp_path)
    approved_contract("sess", ["a.json"])
    stray = repo / "stray.json"
    stray.write_text("{}\n", encoding="utf-8")

    run_hook("post_edit_check.py", edit_json(repo, "stray.json"), hook_env, repo)
    assert run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo).get(
        "decision"
    ) == "block"

    run_hook(
        "session_start.py",
        {"session_id": "sess", "cwd": str(repo), "source": "startup"},
        hook_env,
        repo,
    )
    run_hook("post_edit_check.py", edit_json(repo, "stray.json"), hook_env, repo)

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") == "block", (
        "a new session must re-check scope, not inherit the last one's exemptions"
    )


def test_after_giving_up_the_suite_is_not_re_run_on_an_unchanged_turn(data_dir, hook_env, tmp_path):
    """The ceiling bounds the blocks; this is what bounds the cost.

    Moving the ceiling below the checks is what lets the counter reset, but it
    also means every later turn of a red session pays for a full project run
    that cannot block and is thrown away — measured at 5.8s against 0.09s on a
    two-second suite, repeating for the rest of the session. Any real edit moves
    the counts and forces a genuine run, so this cannot mask new breakage the
    hooks can see.
    """
    import state

    repo = failing_python_repo(tmp_path)
    run_hook("post_edit_check.py", edit_json(repo, "a.json"), hook_env, repo)
    break_the_suite(repo)

    with state.session_state("sess") as session:
        session["consecutive_stop_blocks"] = 3
        session["heavy_blocked"] = {"older": "pytest"}

    run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)
    assert stop_lines(data_dir)[-1]["outcome"] == "giving up after repeats"

    run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert stop_lines(data_dir)[-1]["outcome"] == "skipped: already gave up at this file and line count"


def test_a_git_that_refuses_to_answer_reports_the_stray(data_dir, hook_env, tmp_path):
    """The stray suppression must fail closed, and `cat-file` could not.

    `git cat-file -e HEAD:<path>` exits 128 for "absent from HEAD" *and* for
    "not a git repository", "dubious ownership" and an unborn HEAD. Treating a
    non-zero exit as "never tracked" turned every git refusal into a blanket
    amnesty for missing files. Here HEAD does not exist at all, which is exactly
    the ambiguous case.
    """
    repo = tmp_path / "unborn"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True, capture_output=True)
    approved_contract("sess", ["a.json"])

    stray = repo / "stray.json"
    stray.write_text("{}\n", encoding="utf-8")
    run_hook("post_edit_check.py", edit_json(repo, "stray.json"), hook_env, repo)
    stray.unlink()

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") == "block", (
        "git could not answer, so the stray must be reported rather than forgiven"
    )


def test_a_second_stray_is_reported_even_after_the_first_was(data_dir, hook_env, tmp_path):
    """The fence fires once per session and then stops, which is a fail-open.

    `scope_reported` is a boolean set to True on the first block and cleared
    nowhere in the tree. So the cheapest way past the scope fence is to trip it
    once: stray a single file, take the block, then edit as many unagreed files
    as you like with the gate reporting clean.
    """
    repo = plain_repo(tmp_path)
    approved_contract("sess", ["a.json"])

    first_stray = repo / "one.json"
    first_stray.write_text("{}\n", encoding="utf-8")
    run_hook("post_edit_check.py", edit_json(repo, "one.json"), hook_env, repo)
    blocked = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)
    assert blocked.get("decision") == "block"

    second_stray = repo / "two.json"
    second_stray.write_text("{}\n", encoding="utf-8")
    run_hook("post_edit_check.py", edit_json(repo, "two.json"), hook_env, repo)

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") == "block", "a new stray must be reported, not waved through"
    assert "two.json" in response["hookSpecificOutput"]["additionalContext"]


def test_a_file_created_and_deleted_in_one_turn_is_not_an_eternal_stray(data_dir, hook_env, tmp_path):
    """`files_touched` is append-only, so a temp file is accused forever.

    Hit for real: a reviewer subagent wrote a scratch test, deleted it, and the
    Stop gate then blocked on a path with nothing behind it — a demand the model
    cannot satisfy, costing a turn and one of its three attempts every time.

    The suppression has to be narrow. Deleting a file that *is* tracked at HEAD
    is genuine scope creep and must still be reported, which is what the second
    half of this test pins.
    """
    repo = plain_repo(tmp_path)
    approved_contract("sess", ["a.json"])

    scratch = repo / "tmp_scratch.json"
    scratch.write_text("{}\n", encoding="utf-8")
    run_hook("post_edit_check.py", edit_json(repo, "tmp_scratch.json"), hook_env, repo)
    scratch.unlink()

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") != "block", (
        "a file that never reached HEAD and no longer exists is not a stray"
    )

    tracked = repo / "a.json"  # committed by `plain_repo`, and not in scope? it is.
    committed_stray = repo / "gone.json"
    committed_stray.write_text("{}\n", encoding="utf-8")
    for argv in (["git", "add", "-A"], ["git", "commit", "-qm", "adds gone.json"]):
        subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)
    run_hook("post_edit_check.py", edit_json(repo, "gone.json"), hook_env, repo)
    committed_stray.unlink()

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") == "block", (
        "deleting a tracked file the plan never named is scope creep and must be reported"
    )
    assert "gone.json" in response["hookSpecificOutput"]["additionalContext"]
    assert tracked.exists()


def test_an_unchanged_turn_still_takes_the_cheap_skip(data_dir, hook_env, tmp_path):
    """The other end of the same key.

    Widening it is only safe if it still repeats when nothing changed. A key
    that never repeats runs the whole project suite at the end of every turn,
    including turns that only answered a question — which is how the gate stops
    being worth having.
    """
    repo = plain_repo(tmp_path)
    run_hook("post_edit_check.py", edit_json(repo, "a.json"), hook_env, repo)
    run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert stop_lines(data_dir)[-1]["outcome"].startswith("skipped")


# --- what the block message claims to know ------------------------------------


def test_a_failure_proved_absent_at_head_is_reported_as_this_session_s(
    data_dir, hook_env, tmp_path
):
    """The gate builds a clean worktree at HEAD and re-runs the check there, so
    when that comes back green it *knows* the session caused the failure.

    It then told the model "if this failure predates your changes, say so plainly
    instead of trying to fix it" — hedging about a question it had just answered.
    The model spends the turn investigating provenance rather than the bug.
    """
    repo = failing_python_repo(tmp_path)
    run_hook("post_edit_check.py", edit_json(repo, "a.json"), hook_env, repo)
    break_the_suite(repo)

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") == "block"
    context = response["hookSpecificOutput"]["additionalContext"]
    assert "does not reproduce on a clean checkout of HEAD" in context, context
    assert "NOT compared" not in context, "the comparison ran; the message must not say otherwise"


def test_a_failure_that_could_not_be_compared_says_so_instead_of_guessing(
    data_dir, hook_env, tmp_path
):
    """The same message, where the comparison is impossible.

    Outside a git repository there is no HEAD to build a worktree from, so
    `project_check_at_head` returns None — "could not answer", which is a
    different fact from "HEAD is clean". Both used to produce the identical
    sentence, and the branch that hedged was the honest one only by accident.

    Failing closed and blocking is still right: the check really did fail. What
    must not happen is the gate implying it knows whose failure it is.
    """
    repo = failing_python_repo(tmp_path)
    shutil.rmtree(repo / ".git")
    run_hook("post_edit_check.py", edit_json(repo, "a.json"), hook_env, repo)
    break_the_suite(repo)

    response = run_hook("stop_gate.py", {"session_id": "sess", "cwd": str(repo)}, hook_env, repo)

    assert response.get("decision") == "block", "a real failure must still block"
    context = response["hookSpecificOutput"]["additionalContext"]
    assert "NOT compared against HEAD" in context, context
    assert "so this session caused it" not in context, (
        "nothing established that; claiming it sends the model after the wrong bug"
    )
