#!/usr/bin/env python3
"""PreToolUse hook: ask for a contract once a change stops being small.

The user asked to be consulted before non-trivial work, and to be left alone on
one-liners. Those two requirements are in tension, and the resolution is that
size decides, not intent — intent is not observable from a hook, but the number
of files and lines a session has already changed is.

So the first edits of any session pass without comment. Once a session crosses
the threshold where it is plainly no longer a small change, and no approved
contract exists, the gate asks — **once**. Not once per edit: a gate that
interrupts repeatedly gets answered reflexively, which is the same as not asking.

`ask` is used rather than `deny` throughout. Denying would make the model argue
with a wall; asking puts the decision where the user wanted it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import contract as contract_mod
from state import (
    emit,
    gates_disabled,
    guard,
    read_event,
    read_json,
    session_state,
    shard_is_accountable,
    shard_path,
    shards_dir,
    writer_id,
)

# Below both of these, a change is small enough that stopping to agree a
# contract costs more than it saves.
TRIVIAL_FILES = 3
TRIVIAL_LINES = 100

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _target(event: dict) -> str | None:
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    return raw if isinstance(raw, str) and raw.strip() else None


def _respond(decision: str, reason: str) -> None:
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        }
    )


def _other_worker_holding(session_id: str, writer: str, target: str) -> str | None:
    """Another worker that has already written this file, if there is one.

    This is the one failure parallel work adds and the only one it cannot
    report: two workers editing one file, last write wins, no error and no
    conflict marker. It is undetectable afterwards, so it is refused at the edit
    rather than found at the end.

    Only other *workers* count. The lead legitimately touches files before it
    fans out — it writes the failing test first — so counting `main` here would
    deny the worker that owns the test file it just wrote.

    That sentence was unenforceable until shards started carrying `agent`, and it
    was false: reviewers hold `Bash`, so any command of theirs that dirties a
    tracked file — a mutation sweep, a build emitting `dist/`, a formatter — was
    recorded in their own shard, and `files_touched` is append-only, so it stayed
    there. The next worker sent to that file was denied with "already written by
    agent-7, which owns a different slice of this plan". It owns no slice, the
    worker handed the file back, and the lead read it as a real conflict.
    `shard_is_accountable` is the same test `_merge_shards` applies, shared so
    the two cannot drift apart again.
    """
    posix = Path(target).as_posix()
    # This writer's *shard name*, not its raw id. Shard files are named through
    # `_safe`, which rewrites anything outside `[alnum]-_`, so comparing a raw
    # agent id against a sanitised stem would fail to match a worker's own shard —
    # and the worker would then be denied the file it had just written itself,
    # report the slice as one it could not take, and the slice would silently go
    # unbuilt. Every id seen so far survives `_safe` unchanged, which is exactly
    # what makes this the kind of thing that breaks later and quietly.
    own = shard_path(session_id, writer).stem
    for shard in shards_dir(session_id).glob("*.json"):
        other = shard.stem
        if other in (own, "main"):
            continue
        record = read_json(shard, default=None)
        if not isinstance(record, dict) or not shard_is_accountable(record):
            continue
        if any(Path(p).as_posix() == posix for p in (record.get("files_touched") or [])):
            return other
    return None


def main() -> int:
    if gates_disabled():
        return 0

    event = read_event()
    if event.get("tool_name") not in EDIT_TOOLS:
        return 0
    target = _target(event)
    if target is None:
        return 0
    session_id = event.get("session_id", "unknown")
    writer = writer_id(event)
    if writer != "main":
        # A worker is executing a plan that was already approved, and cannot
        # answer a question meant for the user anyway: `ask` inside a subagent
        # surfaces in the lead's session with no context to decide from. The one
        # thing it is stopped for is writing over another worker.
        holder = _other_worker_holding(session_id, writer, target)
        if holder:
            _respond(
                "deny",
                f"{Path(target).name} has already been written by {holder}, which owns a"
                " different slice of this plan. Two workers editing one file lose code"
                " silently — whoever writes last wins. Report this file as one you could"
                " not take rather than editing it."
            )
        return 0

    existing = contract_mod.load(session_id)

    with session_state(session_id) as session:
        touched = set(session.get("files_touched") or [])
        projected = touched | {target}
        lines = int(session.get("lines_changed") or 0)

        if existing and existing.approved:
            # Scope is enforced at end of turn, where the full picture of what
            # changed is available. Interrupting mid-edit on a path the model is
            # part-way through arguing for is not useful.
            return 0

        small = len(projected) <= TRIVIAL_FILES and lines <= TRIVIAL_LINES
        if small or session.get("edit_gate_prompted"):
            return 0

        session["edit_gate_prompted"] = True

    if existing and not existing.approved:
        _respond(
            "ask",
            "A contract was written for this session but has not been approved yet."
            " Approving here also approves the plan it describes.",
        )
        return 0

    _respond(
        "ask",
        f"This session has now changed {len(projected)} files and about {lines} lines,"
        " which is past the point where the harness expects an agreed contract."
        " Allow to continue without one, or deny and ask for /harness:plan first."
        " You will not be asked again this session."
    )
    return 0


if __name__ == "__main__":
    with guard("pre_edit_gate"):
        sys.exit(main())
