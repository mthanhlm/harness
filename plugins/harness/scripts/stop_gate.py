#!/usr/bin/env python3
"""Stop hook: refuse to end a turn that left the project broken.

The per-edit gate only sees one file at a time, so it cannot catch the failures
that matter most: a change that type-checks locally but breaks a caller, or
passes lint but fails the tests. Those only show up project-wide, and running
project-wide checks after every edit would be unusable. So they run once, here,
when the model thinks it is finished.

Blocking here is powerful and therefore dangerous, so it is bounded three ways:

- **Nothing to check, nothing to run.** A session that has edited no files skips
  entirely. Note the scope: `files_touched` is per session and append-only, so
  after the first edit this stops firing — a later conversational turn is
  skipped by the unchanged-counts test below, not by this one.
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
import subprocess
import sys
import time
from pathlib import Path

import contract as contract_mod
from detect import get_profile, heavy_checks
from runner import BASELINE_COST_CEILING, project_check_at_head, run_project_check, trim
from state import emit, gates_disabled, guard, read_event, repo_root, session_state, trace

MAX_CONSECUTIVE_BLOCKS = 3

# Rough cost order. Running the cheapest first means a broken build is usually
# reported after a two-second type-check rather than a minute-long bundle.
COST_ORDER = {"typecheck": 0, "lint": 1, "test": 2, "build": 3}


def _signature(label: str, output: str) -> str:
    return hashlib.sha256(f"{label}\n{output}".encode("utf-8")).hexdigest()[:16]


def _out_of_scope(session_id: str, touched: list[str], root: Path, base: str = "") -> list[str]:
    """Files changed that the approved contract did not list.

    Scope creep is the cheapest thing here to detect and among the most
    expensive to review, because the extra changes are plausible on their own
    and only look wrong next to what was agreed.

    The contract names repo-relative paths and the session records absolute
    ones, so a touched path is made relative to `root` and compared against
    each scoped entry exactly — `src/lib/other-utils.ts` no longer matches a
    contract that scoped `utils.ts` just because one is a suffix of the other.
    A path that does not sit under `root` cannot be made relative to it, so it
    falls back to an anchored `endswith("/" + entry)`: this is deliberately
    not a resolution step, because resolving would wrongly accuse on a
    symlinked tree where the touched path and the repo root disagree about
    which real directory they are in.

    Normalising with `removeprefix` rather than `lstrip`, because `lstrip` takes
    a *set* of characters: it turned `.github/workflows/ci.yml` into
    `github/...` and `.env.example` into `env.example`. Under the old suffix
    match that mangling cancelled out and nobody saw it; against an exact
    comparison it means a contract can no longer scope any dotfile, and the gate
    blocks the turn over a file the plan named.

    Two kinds of path fail the scope test and are still not reported, both for
    the same reason — there is nothing in the diff to review and nothing left to
    undo, so naming them is an instruction nothing can carry out. See
    `_vanished` for one this session created and destroyed, and
    `_unchanged_since` for one it edited and put back.
    """
    agreed = contract_mod.load(session_id)
    if not agreed or not agreed.approved:
        return []
    scoped = [entry.removeprefix("./").lstrip("/") for entry in agreed.scoped_files]
    if not scoped:
        return []
    root_posix = root.as_posix().rstrip("/")
    strays = []
    for path in touched:
        posix = Path(path).as_posix()
        relative = None
        if posix == root_posix or posix.startswith(root_posix + "/"):
            relative = posix[len(root_posix) + 1 :]
            in_scope = relative in scoped
        else:
            in_scope = any(posix.endswith("/" + entry) for entry in scoped)
        if (
            not in_scope
            and not _vanished(posix, relative, root)
            and not _unchanged_since(relative, root, base)
        ):
            strays.append(posix)
    return strays


def _unchanged_since(relative: str | None, root: Path, base: str) -> bool:
    """Whether this path is byte-identical to what the session started from.

    `files_touched` is append-only — no recorder can un-record — so a file that
    was edited and then put back stays on the ledger and is named as a stray for
    the rest of the session. There is nothing in the diff to review and nothing
    for the model to revert, and the gate blocks the turn demanding it anyway.
    Observed for real: a reviewer subagent mutation-tested `conftest.py`,
    restored it byte for byte, and the fence kept reporting it.

    This is `_vanished`'s other half. That one forgives a path that no longer
    exists; this one forgives a path that exists and matches. Neither forgives a
    change.

    Two things keep the suppression narrow:

    - **The comparison is against the commit the session opened at, never
      HEAD.** Against HEAD, a model that edits an unagreed file and commits
      inside the turn compares its change to itself, comes out clean and walks
      through the fence — the same moving-target hole `_vanished` gave up
      `cat-file -e HEAD:` to escape.
    - **Untracked paths are never suppressed.** `git diff` says nothing about a
      file git does not track, so a brand-new unagreed file compares equal to
      everything; the `ls-files` probe is what separates "put back" from "never
      known". An unagreed new file is scope creep in its purest form.

    No base commit means no fixed point. That reports, like every other thing
    here that cannot be proved, because failing closed is the only safe
    direction for a fence.
    """
    if not base or relative is None:
        return False
    try:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if tracked.returncode != 0:
            return False
        probe = subprocess.run(
            ["git", "-C", str(root), "diff", "--quiet", base, "--", relative],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False  # Cannot prove it is unchanged, so report it.
    return probe.returncode == 0


def _vanished(posix: str, relative: str | None, root: Path) -> bool:
    """Whether this path is a temp file that was created and deleted this turn.

    `files_touched` is append-only — no recorder can know about a deletion that
    has not happened yet — so a scratch file written and removed inside one turn
    stays on the ledger and is reported as a stray for the rest of the session.
    That is a demand the model cannot satisfy: the file is already gone. It has
    happened for real, when a reviewer subagent mutation-tested through Bash.

    The suppression has to be narrow, and an existence check alone is not.
    Deleting a *tracked* file the plan never named is genuine scope creep and is
    among the changes most worth reporting.

    So the question is "did git ever know this path", not "does HEAD have it
    now". `cat-file -e HEAD:<path>` answers the second, and HEAD moves: a model
    that deletes an unagreed tracked file and commits within the turn empties it
    out of HEAD, and the deletion is then forgiven — the exact case above. It
    also cannot distinguish "absent from HEAD" from "not a git repository",
    "dubious ownership" or an unborn HEAD, all of which exit 128, so every git
    refusal became a blanket amnesty.

    `rev-list` answers the first question and both problems go away, with no new
    state to keep: empty output means no commit ever contained this path.
    Anything else — including a non-zero exit — reports the stray, because
    failing closed is the only safe direction for a fence.
    """
    if Path(posix).exists() or relative is None:
        return False
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-list", "-1", "HEAD", "--", relative],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False  # Cannot prove it was never tracked, so report it.
    return probe.returncode == 0 and not probe.stdout.strip()


def _pending_contract_note(session_id: str) -> str | None:
    """Why the scope fence is silent even though a plan exists.

    `_out_of_scope` returns nothing for an unapproved contract, so writing a
    plan and never approving it is strictly worse than not planning: the
    appearance of a fence with none of the effect. Nothing said so, and three of
    six contracts in one day sat pending while edits landed against them.
    """
    agreed = contract_mod.load(session_id)
    if not agreed or agreed.approved:
        return None
    return (
        "a plan was written but never approved, so the scope fence is not"
        " active — set `status: approved` in the contract to arm it"
    )


def _run_until_failure(checks: list[dict], root: Path) -> tuple[list, list, list]:
    """Run project checks cheapest-first, stopping at the first blocking failure.

    There is no value in spending a minute on a bundle when the type-checker has
    already found the reason the turn is not finished.

    A blocking failure is only believed once it has been shown not to reproduce
    on a pristine HEAD. A project that was already broken when the session
    started is not this turn's problem, and saying otherwise sends the model off
    to fix something nobody asked about.
    """
    checks = sorted(checks, key=lambda c: COST_ORDER.get(c["kind"], 9))
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
    event = read_event()
    session_id = event.get("session_id", "unknown")

    if gates_disabled():
        trace("Stop", session_id, "skipped: gates off")
        return 0

    with session_state(session_id) as session:
        touched = session.get("files_touched") or []
        if not touched:
            trace("Stop", session_id, "skipped: no files touched this session")
            return 0

        # Re-running project checks when nothing changed since the last run just
        # makes the user wait for an answer they already have — but only when
        # that run passed. After a block the project is still broken, and the
        # model frequently answers without editing (it explains, or reviews, or
        # asks). Skipping then turns a block into a silent pass and lets the turn
        # end red, which is the one outcome this gate exists to prevent.
        # Three counters, because each covers a way of changing the project that
        # the other two cannot see, and any one of them alone lets the gate skip
        # over a red suite:
        #
        # - `lines_changed` moves only for the Edit tools; `post_edit_check` is
        #   its sole writer.
        # - the file count moves when the shell creates something new, but not
        #   when it rewrites a file already on the ledger.
        # - `shell_changes` counts what a command actually altered, before
        #   attribution drops the paths another writer already claimed. It is
        #   the only one that moves for `sed -i` on a file this session has
        #   already edited — reproduced: the gate logged "already passed" with
        #   pytest red.
        #
        # The triple still repeats exactly when nothing changed, which is what
        # keeps a conversational turn from paying for the suite.
        lines_now = int(session.get("lines_changed") or 0)
        shell_now = int(session.get("shell_changes") or 0)
        ran_at = [len(touched), lines_now, shell_now]
        # Once the gate has given up, re-running the suite buys nothing: it
        # cannot block again, so the whole run is thrown away. Measured at 5.8s
        # against 0.09s per no-op turn on a two-second suite, and it repeats for
        # every remaining turn of a red session — the ceiling bounds the blocks
        # but not the cost, unless this is here. Any real edit moves `ran_at`
        # and forces a run, so the counter can still come back down.
        #
        # The one thing given up: a project fixed outside the hooked tools — the
        # user's own editor — is not noticed until the next edit through them.
        if session.get("gave_up_at") == ran_at:
            trace("Stop", session_id, "skipped: already gave up at this file and line count",
                  files=len(touched), lines=lines_now, shell=shell_now)
            return 0
        if session.get("heavy_ran_at") == ran_at and not session.get("heavy_blocked"):
            trace("Stop", session_id, "skipped: already passed at this file and line count",
                  files=len(touched), lines=lines_now, shell=shell_now)
            return 0

        blocks = int(session.get("consecutive_stop_blocks") or 0)

        root = repo_root(event.get("cwd") or session.get("repo_root"))

        # Cheaper than any project check, and a more common defect.
        #
        # Reported per stray rather than once per session. `scope_reported` was
        # a boolean that nothing ever cleared, so the cheapest route past the
        # fence was to trip it deliberately: stray one file, take the block,
        # then edit anything at all with the gate reporting clean. Keyed on the
        # set of strays, so re-reporting the same unresolved list does not
        # re-block, but a new one does.
        strays = _out_of_scope(session_id, touched, root, str(session.get("base_commit") or ""))
        # Coerced rather than trusted. Before 0.7.0 this key held the boolean
        # `True`, and `set(True)` raises — inside `guard()`, so the hook would
        # exit 0 having done nothing at all: no scope check, no project checks,
        # for every remaining turn of that session. Four session files on the
        # machine this shipped from already held the old value.
        stored = session.get("scope_reported")
        reported_strays = set(stored) if isinstance(stored, list) else set()
        unreported = [s for s in strays if s not in reported_strays]
        if unreported:
            session["scope_reported"] = sorted(reported_strays | set(strays))
            # Deliberately NOT recording `heavy_ran_at` here. This branch returns
            # before a single project check has run, and the skip at the top of
            # the next Stop is "same counts and nothing blocking" — a scope block
            # sets no `heavy_blocked`, so writing the counts made the next turn
            # skip the suite entirely and log "already passed". Nothing passed.
            listed = "\n".join(f"  - {s}" for s in strays)
            emit(
                {
                    "decision": "block",
                    "reason": "changes outside the agreed scope",
                    "hookSpecificOutput": {
                        "hookEventName": "Stop",
                        "additionalContext": (
                            "These files were changed but are not in the contract's scope:\n"
                            f"{listed}\n\n"
                            "Either revert them, or explain why they were genuinely required"
                            " and amend the plan's Scope section to include them."
                            " Do not leave unagreed changes in the diff."
                        ),
                    },
                }
            )
            return 0

        profile = get_profile(root)
        checks = heavy_checks(profile)
        pending = _pending_contract_note(session_id)

        # "Passed" and "nothing ran" are not the same fact, and reporting them
        # with the same word is the failure this gate exists to prevent. The old
        # branch called an empty check list a pass.
        if not checks:
            session["heavy_ran_at"] = ran_at
            trace("Stop", session_id, "nothing to verify", files=len(touched))
            notes = ["nothing to verify — this project defines no checks the harness can run"]
            if pending:
                notes.append(pending)
            emit({"systemMessage": f"harness: {'; '.join(notes)}.", "suppressOutput": True})
            return 0

        trace("Stop", session_id, "running project checks",
              files=len(touched), lines=lines_now)

        passed, failed, inherited = _run_until_failure(checks, root)
        session["heavy_ran_at"] = ran_at

        blocking = [r for r in failed if r.check.get("blocking", True)]
        if not blocking:
            trace("Stop", session_id, "passed",
                  ok=[r.label for r in passed],
                  inherited=[r.label for r in inherited])
            session["consecutive_stop_blocks"] = 0
            # Whatever was broken is fixed; forget it, so a later unchanged turn
            # can take the cheap skip again instead of re-running the suite.
            session["heavy_blocked"] = {}
            notes = [f"{r.label} reports issues" for r in failed]
            notes += [f"{r.label} was already failing before this session" for r in inherited]
            if pending:
                notes.append(pending)
            if notes:
                emit({"systemMessage": f"harness: {'; '.join(notes)} (not blocking).", "suppressOutput": True})
            return 0

        result = blocking[0]
        output = trim(result.output, 3000)
        signature = _signature(result.label, output)
        reported = session.get("heavy_blocked") or {}
        seen_before = signature in reported

        # The same failure twice is not a reason to give up on the first repeat.
        # It is a reason to say so more firmly, and to stop only once the model
        # has genuinely had its three attempts. Bailing at the first repeat means
        # a model that answers without fixing gets the turn ended for it, which
        # is how a broken project reaches the user with the gate reporting clean.
        #
        # The ceiling is enforced here rather than at the top of the hook. Up
        # there it returned before any check ran, so it could never reach the
        # reset above — the counter only ever went up, and a session that had
        # been blocked three times had a dead gate for the rest of its life,
        # including for breakage introduced afterwards. Every later turn was
        # told "still failing" whether or not anything was failing. Counting
        # *consecutive* failures requires running the check that would end the
        # run of them.
        attempts = blocks + 1
        if blocks >= MAX_CONSECUTIVE_BLOCKS or (seen_before and attempts >= MAX_CONSECUTIVE_BLOCKS):
            trace("Stop", session_id, "giving up after repeats", check=result.label,
                  attempts=attempts)
            # `heavy_ran_at` is deliberately not written here: it was already set
            # to this same `ran_at` when the checks finished above, so the line
            # that used to sit here could never be observed.
            session["gave_up_at"] = ran_at
            emit(
                {
                    "systemMessage": (
                        f"harness: {result.label} still failing after {attempts} attempts —"
                        " letting the turn end. The project is broken; run /harness:switch off"
                        " if you want to work on it without the gate."
                    )
                }
            )
            return 0

        trace("Stop", session_id, "BLOCKING", check=result.label,
              attempt=attempts, repeat=seen_before)
        reported[signature] = result.label
        session["heavy_blocked"] = reported
        session["consecutive_stop_blocks"] = attempts

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
