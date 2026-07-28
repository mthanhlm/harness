#!/usr/bin/env python3
"""PostToolUse hook: notice files a shell command changed.

Every other gate hangs off `files_touched`, and until now exactly one thing
wrote to it — the edit hook, matched on `Edit|Write|MultiEdit|NotebookEdit`. So
a change made with `sed -i`, a redirect or a script reached none of it: not the
scope fence, not the per-worker check, not the end-of-turn gate, which skips
entirely when it believes nothing was touched. The gate then reports clean on
work it never saw.

That was tolerable while editing happened on the main thread, where the user
sees each command. It stopped being tolerable when `worker` subagents got
`Bash` and started running unattended.

Two decisions worth stating:

**Ask git, do not read the command.** Deciding whether a command writes by
looking at it is a blocklist, and every miss is silent — the exact failure this
exists to close. `git status` answers the question directly.

**Attribute a file once.** A path already recorded by anyone is not claimed
again, so a worker's shell command cannot take credit — or blame — for another
worker's file. The baseline is fixed at session start rather than moved along,
which means no writer has to update shared state and there is nothing to race.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from state import (
    gates_disabled,
    guard,
    load_session,
    read_event,
    repo_root,
    session_state,
    trace,
    writer_id,
)

# A command that rewrites half the tree is a build or a checkout, not an edit.
# Recording it would bury the scope fence in noise it cannot act on.
MAX_NEW_FILES = 40


def _porcelain(root: Path) -> set[str] | None:
    """Repo-relative paths git considers changed, or None if this is not a repo."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None

    paths = set()
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:]
        # A rename is reported as "old -> new"; only the destination exists now.
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        paths.add(entry.strip().strip('"'))
    return paths


def main() -> int:
    if gates_disabled():
        return 0

    event = read_event()
    if event.get("tool_name") != "Bash":
        return 0

    session_id = event.get("session_id", "unknown")
    root = repo_root(event.get("cwd"))
    current = _porcelain(root)
    if current is None:
        trace("Bash", session_id, "skipped: not a git repo")
        return 0

    session = load_session(session_id)
    baseline = set(session.get("bash_baseline") or [])
    # Absolute, because that is what the edit hook records and what the scope
    # fence and the per-worker check both expect to compare against.
    known = {Path(p).as_posix() for p in (session.get("files_touched") or [])}

    fresh = []
    for rel in sorted(current - baseline):
        absolute = (root / rel).as_posix()
        if absolute not in known:
            fresh.append(absolute)

    if not fresh:
        return 0

    writer = writer_id(event)
    if len(fresh) > MAX_NEW_FILES:
        trace("Bash", session_id, "skipped: too many files changed at once",
              agent=writer[:8], files=len(fresh))
        return 0

    with session_state(session_id, writer) as state:
        state["files_touched"] = sorted(set(state.get("files_touched") or []) | set(fresh))

    trace("Bash", session_id, "recorded", agent=writer[:8], files=len(fresh))
    return 0


if __name__ == "__main__":
    with guard("bash_watch"):
        sys.exit(main())
