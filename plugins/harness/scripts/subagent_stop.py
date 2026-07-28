#!/usr/bin/env python3
"""SubagentStop hook: check a worker's own slice before it reports back.

The per-edit gate sees one file at a time, so it cannot catch the case where a
worker's later edit breaks a file it wrote earlier — the check on `b.py` never
looks at `a.py`. Without this hook that only surfaces at end of turn, in the
merged diff of every worker, where nobody can tell whose change caused it.

So each worker verifies its own slice at its own boundary, while the context
that wrote it is still live and the blame is unambiguous.

Two things keep it cheap and safe:

- **Only this worker's files.** Read from the worker's own state shard, never
  the merged view. Blocking a worker for another worker's breakage is the
  failure this hook would otherwise introduce.
- **Once per worker.** A worker that cannot fix its slice hands back rather than
  spinning, which is the same bound the end-of-turn gate uses and for the same
  reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

from detect import checks_for_file, get_profile
from runner import run_file_check, trim
from state import (
    emit,
    gates_disabled,
    guard,
    read_event,
    read_json,
    repo_root,
    shard_path,
    trace,
    write_json,
    writer_id,
)

# A worker's slice is meant to be a handful of files. Checking more than this
# means the split was too coarse, and stalling its hand-back does not fix that.
MAX_FILES = 12


def _own_files(session_id: str, writer: str) -> list[str]:
    stored = read_json(shard_path(session_id, writer), default=None)
    if not isinstance(stored, dict):
        return []
    files = stored.get("files_touched")
    return [f for f in files if isinstance(f, str)] if isinstance(files, list) else []


def _block_reason(failures: list[tuple[str, object]]) -> str:
    lines = ["Your own edits left these problems behind:", ""]
    for name, result in failures:
        lines.append(f"[{name} — {result.label}]")
        for diagnostic in result.new_diagnostics[:8]:
            lines.append(f"  {diagnostic}")
        if not result.new_diagnostics:
            lines.append(trim(result.output, 600))
        lines.append("")
    lines.append(
        "Fix these before reporting back. They are in files you edited, and they were"
        " not there at HEAD. If you cannot fix them, say so plainly in your result"
        " rather than reporting the work as finished."
    )
    return "\n".join(lines)


def main() -> int:
    if gates_disabled():
        return 0

    event = read_event()
    session_id = event.get("session_id", "unknown")
    writer = writer_id(event)
    if writer == "main":
        # No agent id on the payload means the slice cannot be identified. The
        # merged view is not a stand-in: checking it would blame this worker for
        # every other worker's files.
        trace("SubagentStop", session_id, "skipped: no agent id on payload")
        return 0

    path = shard_path(session_id, writer)
    shard = read_json(path, default=None)
    if isinstance(shard, dict) and shard.get("stop_checked"):
        trace("SubagentStop", session_id, "skipped: already checked", agent=writer[:8])
        return 0

    files = _own_files(session_id, writer)
    if not files:
        # Every read-only agent lands here — reviewers, the refuter, the
        # architect. Nothing was written, so there is nothing to verify.
        trace("SubagentStop", session_id, "skipped: wrote nothing", agent=writer[:8])
        return 0

    root = repo_root(event.get("cwd"))
    inside = root.resolve()
    profile = get_profile(root)

    failures: list[tuple[str, object]] = []
    for raw in files[:MAX_FILES]:
        # Paths are re-resolved here, minutes after they were recorded, so the
        # containment check is not redundant: a path that has since become a
        # symlink out of the tree would otherwise have the repo's tools run
        # against its target, and the output echoed into the transcript.
        target = Path(raw).resolve()
        if not target.is_file() or inside not in target.parents:
            continue
        for check in checks_for_file(profile, str(target)):
            result = run_file_check(check, root, target)
            if result.blocking:
                failures.append((target.name, result))

    if isinstance(shard, dict):
        shard["stop_checked"] = True
        write_json(path, shard)

    # A slice this wide was cut wrong, and silently checking part of it would
    # read as "verified" when most of it never was.
    skipped = max(0, len(files) - MAX_FILES)
    trace("SubagentStop", session_id, "blocked" if failures else "ok",
          agent=writer[:8], files=len(files), failed=len(failures), unchecked=skipped)

    if failures:
        emit({"decision": "block", "reason": _block_reason(failures)})
    elif skipped:
        emit(
            {
                "systemMessage": (
                    f"harness: only {MAX_FILES} of {len(files)} files a worker touched were"
                    " checked — that slice was cut too wide to verify."
                ),
                "suppressOutput": True,
            }
        )
    return 0


if __name__ == "__main__":
    with guard("subagent_stop"):
        sys.exit(main())
