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
from state import emit, gates_disabled, guard, read_event, session_state

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


def _ask(reason: str) -> None:
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason,
            }
        }
    )


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
        _ask(
            "A contract was written for this session but has not been approved yet."
            " Approving here also approves the plan it describes."
        )
        return 0

    _ask(
        f"This session has now changed {len(projected)} files and about {lines} lines,"
        " which is past the point where the harness expects an agreed contract."
        " Allow to continue without one, or deny and ask for /harness:plan first."
        " You will not be asked again this session."
    )
    return 0


if __name__ == "__main__":
    with guard("pre_edit_gate"):
        sys.exit(main())
