#!/usr/bin/env python3
"""Stop hook: refuse to end a turn that left the project broken.

The per-edit gate only sees one file at a time, so it cannot catch the failures
that matter most: a change that type-checks locally but breaks a caller, or
passes lint but fails the tests. Those only show up project-wide, and running
project-wide checks after every edit would be unusable. So they run once, here,
when the model thinks it is finished.

Blocking here is powerful and therefore dangerous, so it is bounded three ways:

- **Nothing to check, nothing to run.** A turn that edited no files skips
  entirely, which is most conversational turns.
- **Never twice for the same failure.** If a check fails with output identical
  to a failure already reported this session, the model has had its chance and
  is evidently not fixing it. Reporting it again just burns turns.
- **A hard ceiling of three consecutive blocks.** Claude Code force-ends a turn
  after eight, but by then the user has paid for eight failed attempts. Three is
  where a stuck loop stops being worth the tokens, and the problem is handed to
  the user instead.

The last two exist because the alternative is the failure mode the user named
directly: an agent spinning on something it cannot fix, at Opus prices.
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

from detect import get_profile, heavy_checks
from runner import BASELINE_COST_CEILING, project_check_at_head, run_project_check, trim
from state import emit, gates_disabled, guard, read_event, repo_root, session_state

MAX_CONSECUTIVE_BLOCKS = 3

# Rough cost order. Running the cheapest first means a broken build is usually
# reported after a two-second type-check rather than a minute-long bundle.
COST_ORDER = {"typecheck": 0, "lint": 1, "test": 2, "build": 3}


def _signature(label: str, output: str) -> str:
    return hashlib.sha256(f"{label}\n{output}".encode("utf-8")).hexdigest()[:16]


def _run_until_failure(profile: dict, root: Path) -> tuple[list, list, list]:
    """Run project checks cheapest-first, stopping at the first blocking failure.

    There is no value in spending a minute on a bundle when the type-checker has
    already found the reason the turn is not finished.

    A blocking failure is only believed once it has been shown not to reproduce
    on a pristine HEAD. A project that was already broken when the session
    started is not this turn's problem, and saying otherwise sends the model off
    to fix something nobody asked about.
    """
    checks = sorted(heavy_checks(profile), key=lambda c: COST_ORDER.get(c["kind"], 9))
    passed, failed, inherited = [], [], []
    for check in checks:
        started = time.monotonic()
        result = run_project_check(check, root)
        if result.ok:
            passed.append(result)
            continue
        if not check.get("blocking", True):
            failed.append(result)
            continue

        elapsed = time.monotonic() - started
        if elapsed <= BASELINE_COST_CEILING:
            broken_at_head = project_check_at_head(check, root, int(elapsed * 3) + 30)
            if broken_at_head:
                inherited.append(result)
                continue
        failed.append(result)
        break
    return passed, failed, inherited


def main() -> int:
    if gates_disabled():
        return 0

    event = read_event()
    session_id = event.get("session_id", "unknown")

    with session_state(session_id) as session:
        touched = session.get("files_touched") or []
        if not touched:
            return 0

        # Re-running project checks when nothing changed since the last run just
        # makes the user wait for an answer they already have.
        lines_now = int(session.get("lines_changed") or 0)
        if session.get("heavy_ran_at") == lines_now:
            return 0

        blocks = int(session.get("consecutive_stop_blocks") or 0)
        if blocks >= MAX_CONSECUTIVE_BLOCKS:
            session["heavy_ran_at"] = lines_now
            emit(
                {
                    "systemMessage": (
                        f"harness: still failing after {blocks} attempts — letting the turn end."
                        " The problem is yours to look at; run /harness:switch off to stop the"
                        " gate nagging while you do."
                    )
                }
            )
            return 0

        root = repo_root(event.get("cwd") or session.get("repo_root"))
        profile = get_profile(root)
        passed, failed, inherited = _run_until_failure(profile, root)
        session["heavy_ran_at"] = lines_now

        blocking = [r for r in failed if r.check.get("blocking", True)]
        if not blocking:
            session["consecutive_stop_blocks"] = 0
            notes = [f"{r.label} reports issues" for r in failed]
            notes += [f"{r.label} was already failing before this session" for r in inherited]
            if notes:
                emit({"systemMessage": f"harness: {'; '.join(notes)} (not blocking).", "suppressOutput": True})
            return 0

        result = blocking[0]
        output = trim(result.output, 3000)
        signature = _signature(result.label, output)
        reported = session.get("heavy_blocked") or {}

        if signature in reported:
            # Already handed this exact failure to the model once. Repeating it
            # produces the same failed attempt at the same price.
            session["consecutive_stop_blocks"] = 0
            emit(
                {
                    "systemMessage": (
                        f"harness: {result.label} is still failing with the same output as before."
                        " Letting the turn end rather than looping on it."
                    )
                }
            )
            return 0

        reported[signature] = result.label
        session["heavy_blocked"] = reported
        session["consecutive_stop_blocks"] = blocks + 1

        verified = ", ".join(r.label for r in passed) or "none"
        emit(
            {
                "decision": "block",
                "reason": f"{result.label} failed",
                "hookSpecificOutput": {
                    "hookEventName": "Stop",
                    "additionalContext": (
                        f"The turn is not finished: `{result.label}` fails across the project.\n\n"
                        f"{output}\n\n"
                        f"Checks that did pass: {verified}.\n"
                        "Fix the cause rather than suppressing the check. If this failure"
                        " predates your changes, say so plainly instead of trying to fix it."
                    ),
                },
            }
        )
    return 0


if __name__ == "__main__":
    with guard("stop_gate"):
        sys.exit(main())
