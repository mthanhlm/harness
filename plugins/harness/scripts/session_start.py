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

import bash_watch
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
        # What was already dirty before this session touched anything. Shell
        # commands are noticed by diffing against this, so it must be taken
        # before any work happens and must not move afterwards.
        session["bash_baseline"] = sorted(bash_watch._porcelain(root) or [])
    save_session(session, reset=fresh)
    trace("SessionStart", event.get("session_id", "?"), "bootstrapped",
          source=event.get("source"), agent=event.get("agent_type"))

    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": _describe(profile),
            }
        }
    )
    return 0


if __name__ == "__main__":
    with guard("session_start"):
        sys.exit(main())
