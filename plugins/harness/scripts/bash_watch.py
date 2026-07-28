#!/usr/bin/env python3
"""Notice files a shell command changed, and only the ones it changed.

Every other gate hangs off `files_touched`, and until recently exactly one thing
wrote to it — the edit hook, matched on `Edit|Write|MultiEdit|NotebookEdit`. So a
change made with `sed -i`, a redirect or a script reached none of it: not the
scope fence, not the per-worker check, and not the end-of-turn gate, which skips
entirely when it believes nothing was touched.

**Attribution is per command, not per session.** The first version of this took
one snapshot at session start and treated everything dirty afterwards as the
agent's doing. That is wrong in the direction that hurts: a file the *user* was
editing in their own window got claimed, and the end-of-turn gate then told the
model to revert the user's uncommitted work. So the tree is sampled immediately
before the command and again immediately after, and only the difference counts.

Two further consequences of doing it that way:

- A file that was **already dirty** is still detected when a command changes it
  again, because the sample records size and mtime rather than just which paths
  git considers modified. Set-difference on paths alone missed exactly the state
  most sessions start in.
- **No sample, no claim.** If the pre-command sample is missing — gates switched
  on mid-session, a resumed session, a hook that failed — nothing is recorded.
  Recording everything would be the flood this exists to prevent.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from state import (
    gates_disabled,
    guard,
    read_event,
    read_json,
    repo_root,
    session_state,
    shard_path,
    trace,
    write_json,
    writer_id,
)

# One command changing more than this is a build, a checkout or a codemod. The
# paths are dropped, but the fact is not: dropping both would let the biggest
# change of the session end the turn with no project check at all.
MAX_NEW_FILES = 40


def _porcelain(root: Path) -> list[str] | None:
    """Repo-relative paths git considers changed, or None outside a repo.

    `-z` because the default output C-quotes and escapes anything non-ASCII —
    `café.py` arrives as `"caf\\303\\251.py"`, which then names no real file, and
    every consumer silently skips it.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "-z", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None

    fields = [f for f in proc.stdout.split("\0") if f]
    paths, index = [], 0
    while index < len(fields):
        entry = fields[index]
        if len(entry) < 4:
            index += 1
            continue
        status, path = entry[:2], entry[3:]
        # A rename emits the new name, then the old one as its own field.
        if status[0] == "R" or status[1] == "R":
            index += 1
        paths.append(path)
        index += 1
    return paths


def snapshot(root: Path) -> dict[str, list[int]] | None:
    """What every changed file looks like right now.

    Size and mtime, not just the path, because a file that was already dirty
    stays dirty — its presence in `git status` does not change when a command
    edits it again, and that is the state most sessions start in.
    """
    paths = _porcelain(root)
    if paths is None:
        return None
    sample: dict[str, list[int]] = {}
    for rel in paths:
        try:
            stat = (root / rel).stat()
            sample[rel] = [stat.st_size, stat.st_mtime_ns]
        except OSError:
            sample[rel] = [-1, -1]  # deleted, and that is a change too
    return sample


def _changed(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(rel for rel, mark in after.items() if before.get(rel) != mark)


def _pre(root: Path, session_id: str, writer: str) -> int:
    """Sample the tree before the command runs."""
    sample = snapshot(root)
    if sample is None:
        return 0
    path = shard_path(session_id, writer)
    stored = read_json(path, default=None)
    shard = stored if isinstance(stored, dict) else {}
    shard["bash_pre"] = sample
    write_json(path, shard)
    return 0


def _post(root: Path, session_id: str, writer: str) -> int:
    """Record what the command actually changed."""
    path = shard_path(session_id, writer)
    stored = read_json(path, default=None)
    shard = stored if isinstance(stored, dict) else {}
    before = shard.get("bash_pre")
    if not isinstance(before, dict):
        # Never sampled, so nothing can be attributed. Claiming the whole dirty
        # tree here is how the user's own uncommitted work gets reverted.
        trace("Bash", session_id, "skipped: no pre-command sample", agent=writer[:8])
        return 0

    after = snapshot(root)
    if after is None:
        return 0

    shard.pop("bash_pre", None)
    write_json(path, shard)

    # A path someone has already recorded is not claimed again. Two workers can
    # sample before the same change, and without this the second one takes the
    # blame for the first one's file.
    from state import load_session

    known = {Path(p).as_posix() for p in (load_session(session_id).get("files_touched") or [])}
    fresh = [rel for rel in _changed(before, after) if (root / rel).as_posix() not in known]
    if not fresh:
        return 0

    dropped = max(0, len(fresh) - MAX_NEW_FILES)
    recorded = [(root / rel).as_posix() for rel in fresh[:MAX_NEW_FILES]]

    with session_state(session_id, writer) as state:
        state["files_touched"] = sorted(set(state.get("files_touched") or []) | set(recorded))

    trace("Bash", session_id, "recorded", agent=writer[:8],
          files=len(recorded), dropped=dropped)

    if dropped:
        from state import emit

        emit(
            {
                "systemMessage": (
                    f"harness: a shell command changed {len(fresh)} files;"
                    f" {dropped} are not being tracked individually."
                ),
                "suppressOutput": True,
            }
        )
    return 0


def main() -> int:
    if gates_disabled():
        return 0

    event = read_event()
    if event.get("tool_name") != "Bash":
        return 0

    session_id = event.get("session_id", "unknown")
    root = repo_root(event.get("cwd"))
    writer = writer_id(event)

    if event.get("hook_event_name") == "PreToolUse":
        return _pre(root, session_id, writer)
    return _post(root, session_id, writer)


if __name__ == "__main__":
    with guard("bash_watch"):
        sys.exit(main())
