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

Two things it is wired to see and one it cannot:

- `PostToolUse` **and** `PostToolUseFailure`, because a command that exits
  non-zero has usually still written something — a build that emits `dist/` and
  then fails its own check, a `sed -i` that dies on the third file having
  rewritten the first two. Without the failure event the pre-command sample is
  simply overwritten by the next command, and those files enter no gate at all.
- A command run with `run_in_background` returns as soon as it is launched, so
  the post-sample is taken before it has written anything. Those changes are
  invisible here. Said out loud rather than left to be discovered: the gate that
  reports at end of turn is the backstop for them.
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
    repo_root,
    session_state,
    shard_update,
    trace,
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
    """Paths the command altered that are still dirty afterwards."""
    return sorted(rel for rel, mark in after.items() if before.get(rel) != mark)


def _undone(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Paths the command returned to their committed state.

    `git checkout -- f`, `git stash` and `git clean` take a path out of
    `git status` altogether, so it is absent from `after` — and `_changed`
    above, which iterates `after` alone, counts it as nothing. That is still a
    change, and it is the one that turns a green suite red whenever it reverts
    half of a pair: undo the implementation, keep the test that calls it.

    These count toward the end-of-turn gate and never toward `files_touched`.
    Claiming them as touched would have the scope fence order the model to
    revert a file that already matches HEAD — an unsatisfiable instruction, and
    the exact harm the per-command sampling described above exists to avoid.
    """
    return sorted(set(before) - set(after))


def _pre(root: Path, session_id: str, writer: str) -> int:
    """Sample the tree before the command runs."""
    sample = snapshot(root)
    if sample is None:
        return 0
    with shard_update(session_id, writer) as shard:
        shard["bash_pre"] = sample
    return 0


def _post(root: Path, session_id: str, writer: str) -> int:
    """Record what the command actually changed."""
    with shard_update(session_id, writer) as shard:
        before = shard.pop("bash_pre", None)
    if not isinstance(before, dict):
        # Never sampled, so nothing can be attributed. Claiming the whole dirty
        # tree here is how the user's own uncommitted work gets reverted.
        #
        # Distinguished from a genuinely lost sample because it is not one:
        # `_porcelain` (and therefore `snapshot`) returns None on every call for
        # a directory that is not a git repository at all, so `_pre` never
        # writes `bash_pre` there and this branch is permanent for the rest of
        # the session — measured at 983 of 1,017 "skipped" traces across real
        # logs, six of nine directories structurally blind to this detector
        # 100% of the time. Logging both the same way reads as one sample that
        # happened not to be taken, and it took breaking the count down per
        # directory to see it was not.
        if snapshot(root) is None:
            trace("Bash", session_id, "skipped: not a git repository", agent=writer[:8])
        else:
            trace("Bash", session_id, "skipped: no pre-command sample", agent=writer[:8])
        return 0

    after = snapshot(root)
    if after is None:
        return 0

    changed = _changed(before, after)
    undone = _undone(before, after)
    if not changed and not undone:
        return 0

    # A path someone has already recorded is not claimed again. Two workers can
    # sample before the same change, and without this the second one takes the
    # blame for the first one's file.
    from state import load_session

    known = {Path(p).as_posix() for p in (load_session(session_id).get("files_touched") or [])}
    fresh = [rel for rel in changed if (root / rel).as_posix() not in known]

    dropped = max(0, len(fresh) - MAX_NEW_FILES)
    recorded = [(root / rel).as_posix() for rel in fresh[:MAX_NEW_FILES]]

    # `changed`, not `fresh`. The end-of-turn gate skips when its counters have
    # not moved, and neither of the other two can move for a shell edit to a
    # file this session already touched: `lines_changed` has one writer and it
    # is `post_edit_check`, and every such path is filtered out of `fresh` just
    # above. So the gate reported "already passed" over a red suite — the
    # plugin's whole promise, inverted. This counter is the one thing that moves
    # for that case, so it counts what the command touched, not what this writer
    # gets to claim.
    with session_state(session_id, writer) as state:
        state["shell_changes"] = (
            int(state.get("shell_changes") or 0) + len(changed) + len(undone)
        )
        if recorded:
            state["files_touched"] = sorted(set(state.get("files_touched") or []) | set(recorded))

    trace("Bash", session_id, "recorded", agent=writer[:8],
          files=len(recorded), changed=len(changed), undone=len(undone), dropped=dropped)

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
    # Both shells. On Windows without Git Bash, Claude Code enables the
    # PowerShell tool and does not register Bash at all, so every shell command
    # arrives under the other name — and a guard naming only `Bash` drops all of
    # them while the matcher in `hooks.json` is what looks like the wiring.
    if event.get("tool_name") not in ("Bash", "PowerShell"):
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
