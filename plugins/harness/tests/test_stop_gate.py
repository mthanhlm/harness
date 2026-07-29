"""The end-of-turn gate, driven as the process Claude Code drives.

Both behaviours here were found in a real `gate.log` rather than by reading the
code, and both fail the same way: the gate reports a good outcome having done
nothing. That is worse than a gate that is switched off, because a switched-off
gate does not tell you the project is fine.
"""

from __future__ import annotations

import json
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

    `_run_until_failure` returns three empty lists when the profile has no
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
