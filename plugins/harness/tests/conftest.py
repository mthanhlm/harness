"""Shared fixtures for the hook script tests.

The hook scripts are all shaped the same way — read JSON on stdin, write JSON on
stdout, keep state under CLAUDE_PLUGIN_DATA — so a test is "point the data
directory somewhere disposable, feed a payload, assert on what came back".

Every fixture here redirects CLAUDE_PLUGIN_DATA. Without that, running the tests
would write into the real plugin data directory and corrupt live session state.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    """A disposable CLAUDE_PLUGIN_DATA for one test.

    `HARNESS_OFF` is cleared as well. It is the plugin's kill switch, and a
    developer who has it set in their shell would otherwise get a suite that
    passes by checking nothing at all.
    """
    path = tmp_path / "plugin-data"
    path.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(path))
    monkeypatch.delenv("HARNESS_OFF", raising=False)
    return path


@pytest.fixture
def run_child(data_dir):
    """Run a snippet against the scripts package in a separate process.

    Concurrency is the whole point of several of these tests, and threads would
    not reproduce it: the failure being guarded against is two OS processes
    reading the same file before either writes.
    """

    def _spawn(source: str, *args: str) -> subprocess.Popen:
        prelude = f"import sys; sys.path.insert(0, {str(SCRIPTS)!r})\n"
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=str(data_dir))
        env.pop("HARNESS_OFF", None)
        return subprocess.Popen(
            [sys.executable, "-c", prelude + source, *args],
            env=env,
            stderr=subprocess.PIPE,
        )

    return _spawn


@pytest.fixture
def hook_env(data_dir):
    """Environment for running a hook script as its own process."""
    env = dict(os.environ, CLAUDE_PLUGIN_DATA=str(data_dir))
    env.pop("HARNESS_OFF", None)
    return env


@pytest.fixture
def git_repo(tmp_path):
    """A repo whose HEAD is clean, so new breakage is attributable to the edit.

    `pytest.ini` is what makes the detector recognise this as a Python project;
    without it no per-file check is configured and every assertion about
    blocking would pass by checking nothing.
    """
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("a.py", "b.py"):
        (root / name).write_text("value = 1\n", encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "base"],
    ):
        subprocess.run(argv, cwd=str(root), check=True, capture_output=True)
    return root


@pytest.fixture
def repo_broken_at_head(git_repo):
    """A repo whose HEAD already contains a file that fails its own checks.

    This is the state most real repos are in, and it is what the plugin's
    central promise is about: a diagnostic that was there before the edit is not
    the edit's fault. Blocking on it fires on every edit in the repo and gets
    the harness switched off, which is what happened to its predecessor.
    """
    broken = git_repo / "legacy.py"
    broken.write_text("def legacy(:\n    return 1\n", encoding="utf-8")
    for argv in (["git", "add", "-A"], ["git", "commit", "-qm", "inherited breakage"]):
        subprocess.run(argv, cwd=str(git_repo), check=True, capture_output=True)
    return git_repo


def run_hook(script: str, payload: dict, env: dict, cwd: Path) -> dict:
    """Drive a hook exactly as Claude Code does: JSON on stdin, JSON on stdout."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        timeout=180,
    )
    assert proc.returncode == 0, f"a hook must never fail a session: {proc.stderr}"
    return json.loads(proc.stdout) if proc.stdout.strip() else {}
