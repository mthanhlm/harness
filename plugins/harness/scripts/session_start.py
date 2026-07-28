#!/usr/bin/env python3
"""SessionStart hook: detect how this repo is checked and say so, once.

The context injected here is deliberately short. It is loaded into every single
session, and the documented failure mode for always-on context is that a long
one causes the important lines to be ignored. Anything that is only sometimes
relevant belongs in a skill, which loads on demand.

What genuinely cannot be inferred from the code, and so earns its place: the
exact commands this repo is checked with, and the fact that some of them run
automatically.
"""

from __future__ import annotations

import sys

import local_ignore
from detect import get_profile
from state import (
    emit,
    gates_disabled,
    guard,
    read_event,
    repo_root,
    save_session,
    load_session,
    trace,
)


def _describe(profile: dict) -> str:
    per_file = [c for c in profile["checks"] if c["scope"] == "file"]
    project = [c for c in profile["checks"] if c["scope"] == "project"]

    def names(checks: list[dict]) -> str:
        seen: list[str] = []
        for check in checks:
            label = check["label"]
            if label not in seen:
                seen.append(label)
        return ", ".join(seen) if seen else "none detected"

    root_name = profile["repo_root"].rsplit("/", 1)[-1]
    lines = [
        f"harness active — {root_name} ({'/'.join(profile['languages'])})",
        f"  after each edit, on the touched file: {names(per_file)}",
        f"  when the turn ends, across the project: {names(project)}",
        "",
        "A check that fails only because of your edit blocks and comes back to you"
        " to fix. Failures already present at HEAD are ignored, so do not go fixing"
        " unrelated problems you did not cause.",
    ]
    if not per_file and not project:
        lines.append(
            "No project tooling was detected, so only syntax checks run."
            " Verification for this repo has to come from something you run yourself."
        )

    withheld = profile.get("withheld_checks") or []
    if withheld:
        # Said out loud every session until it is resolved. Quietly running
        # fewer checks than the user believes is the failure mode this whole
        # boundary would otherwise introduce.
        commands = "; ".join(f"`{' '.join(c.get('argv') or [])}`" for c in withheld[:4])
        lines.append(
            f"\n{len(withheld)} check(s) this repository defines are NOT running: {commands}."
            " They are commands the repo itself supplies rather than ones the harness"
            " composed, so they wait until you have looked at them — cloning a"
            " repository should not run its code. Approve with `/harness:trust`."
            " Until then verification here is weaker than usual, which matters most"
            " in a codebase you do not know."
        )
    return "\n".join(lines)


def _withheld_warning(profile: dict) -> str | None:
    """The same fact as above, addressed to the person who can act on it.

    Granting a repository's commands is the one decision here that only a human
    can make, and `additionalContext` goes to the model. So for a full day it
    was announced 28 times to something that cannot run `/harness:trust`, while
    every project check in every repo stayed switched off and the end-of-turn
    gate reported passes.
    """
    withheld = profile.get("withheld_checks") or []
    if not withheld:
        return None
    commands = ", ".join(f"`{' '.join(c.get('argv') or [])}`" for c in withheld[:3])
    more = f" and {len(withheld) - 3} more" if len(withheld) > 3 else ""
    return (
        f"harness: {len(withheld)} check(s) this repo defines are NOT running"
        f" ({commands}{more}). Nothing here is verified against them until you run"
        " /harness:trust."
    )


def main() -> int:
    if gates_disabled():
        return 0

    event = read_event()
    root = repo_root(event.get("cwd"))
    # Local-only, idempotent, and invisible in any diff — the plugin's own
    # scratch directories are not part of what this repository ships.
    local_ignore.ensure(root)
    profile = get_profile(root)

    session = load_session(event.get("session_id", "unknown"))
    session["repo_root"] = str(root)
    # A resumed session keeps its counters; a fresh one starts clean. The same
    # distinction decides whether each writer's record is cleared: a resume can
    # land mid-fan-out, and wiping a running worker's record would leave its
    # files unverified and its slice reported as empty.
    fresh = event.get("source") not in ("resume", "compact")
    if fresh:
        session["files_touched"] = []
        session["lines_changed"] = 0
        session["consecutive_stop_blocks"] = 0
        session["heavy_blocked"] = {}
    save_session(session, reset=fresh)
    trace("SessionStart", event.get("session_id", "?"), "bootstrapped",
          source=event.get("source"), agent=event.get("agent_type"))

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": _describe(profile),
        }
    }
    warning = _withheld_warning(profile)
    if warning:
        payload["systemMessage"] = warning
    emit(payload)
    return 0


if __name__ == "__main__":
    with guard("session_start"):
        sys.exit(main())
