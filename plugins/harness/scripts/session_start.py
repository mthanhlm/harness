#!/usr/bin/env python3
"""SessionStart hook: detect how this repo is checked and say so, once.

The context injected here is deliberately short. It is loaded into every single
session, and the documented failure mode for always-on context is that a long
one causes the important lines to be ignored. Anything that is only sometimes
relevant belongs in a skill, which loads on demand.

What genuinely cannot be inferred from the code, and so earns its place: the
exact commands this repo is checked with, and the fact that some of them run
automatically.

One thing more, and only when the session did not start from scratch. A
compaction throws the transcript away and a resume never had it; the gates do
not notice, because the contract and the worker shards are both on disk and go
on enforcing whatever was agreed. The model is the part that forgets, and it
then pays to rediscover its own plan — which is how a context that was just
compacted refills. So on `compact`, `resume` and `clear` the few facts it
cannot cheaply reconstruct are handed back. They are read, never written:
nothing here persists anything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import contract as contract_mod
import lessons
import local_ignore
import lsp
import session_end
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

# Only these three arrive with the work already in progress. `startup` must be
# excluded rather than merely uninteresting: session ids are reused, and a
# contract file outlives the turn that wrote it, so injecting on startup hands a
# brand-new session an old plan as though it were current.
RESUMED_SOURCES = ("compact", "resume", "clear")

# Roughly a page. Long enough for the plan, short enough that it does not become
# the thing that gets skimmed past — which is the failure this module's docstring
# is written against.
MAX_LISTED = 12
GOAL_LIMIT = 240
# Lessons are loaded into every session, same as the tool profile, so the cap
# is the same order of magnitude: enough for several entries, not so much that
# the always-on block becomes the thing that gets skimmed past.
LESSONS_LIMIT = 2000


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
    # `_universal_checks` always contributes `json syntax`, so detection alone
    # cannot empty this list. The only way it empties is a repo switching its own
    # checks off — which makes "no tooling was detected" false every time it
    # would have been printed, and worth distinguishing from the real thing.
    suppressed = profile.get("disabled_by_repo") or []
    if suppressed:
        lines.append(
            f"\nThis repository's `.harness.json` switched off: {', '.join(suppressed)}."
            " That is the repo's own choice rather than a detection failure, and"
            " verification here is weaker than the list above suggests."
        )
    elif not per_file and not project:
        lines.append(
            "No project tooling was detected, so only syntax checks run."
            " Verification for this repo has to come from something you run yourself."
        )

    # Said here because it is said nowhere else. A language server whose binary
    # is absent is skipped silently, and the reason surfaces only in the
    # `/plugin` Errors tab. Empty whenever the servers for this repo's languages
    # are already installed, which is the case that has to stay silent.
    advice = lsp.advisory(profile)
    if advice:
        lines.append(f"\n{advice}")
    return "\n".join(lines)


def _contract_line(session_id: str) -> str:
    """Where this session's plan goes, spelled out rather than described.

    The skills used to name it as `${CLAUDE_PLUGIN_DATA}/contracts/${CLAUDE_SESSION_ID}.md`.
    The first half works — Claude Code substitutes `${CLAUDE_PLUGIN_DATA}` into
    skill text before the model sees it. The second half is not a placeholder and
    not a shell variable, so it reached the model verbatim: real transcripts
    contain 225 commands naming a literal `contracts/${CLAUDE_SESSION_ID}.md`.

    Getting it wrong is silent in the worst direction. A contract written under
    any other name is a file `contract.load` never opens, so `approved` is False,
    `scoped_files` is empty, and `stop_gate._out_of_scope` returns nothing —
    the end-of-turn gate then certifies every edit in the session as agreed.

    This hook is handed the session id in its own payload, so it can simply say.
    """
    return (
        "\nThis session's plan contract belongs at exactly:\n"
        f"  {contract_mod.contract_path(session_id)}\n"
        "  That path is what the scope fence reads. A plan written anywhere else"
        " leaves the fence inert while still reporting clean."
    )


def _lessons_block(root: Path) -> str:
    """What this project has already learned, loaded into every session.

    `.harness/lessons.md` is a file in the repository, invites hand editing by
    its own preamble, and in a clone belongs to whoever wrote the repo — so its
    length is not this module's to assume. `lessons.render`'s own `limit` is
    the guard: one hostile or merely huge entry must not be able to flood the
    one block that is loaded into every session start.
    """
    # Asked of the data, not of the rendered sentence. This was a prefix match
    # on `render`'s "nothing recorded yet" line, so rewording that string — a
    # change nothing would flag — would have injected it, plus the revise hint,
    # into every session start of every repo that has no lessons.
    if not lessons.entries(root):
        return ""
    body = lessons.render(root, limit=LESSONS_LIMIT)
    return (
        "\n" + body + "\n"
        "  If one of these turns out to be wrong, say so on the record rather"
        " than just working around it: `lessons.py revise <id>`."
    )


def _listed(entries: list[str], label: str) -> str:
    """One bullet per entry, with an honest tail when the list is cut short.

    Saying "and 34 more" costs six words and keeps the line true. Silently
    showing the first twelve reads as a complete list, and a scope list that
    looks complete but is not is the same failure as no scope list at all.
    """
    shown = entries[:MAX_LISTED]
    lines = [f"  {label}:"] + [f"    - {entry}" for entry in shown]
    if len(entries) > len(shown):
        lines.append(f"    … and {len(entries) - len(shown)} more")
    return "\n".join(lines)


def _excluded(text: str) -> list[str]:
    """The bullets a plan promised not to touch, one line each.

    Its own copy of the bullet-joining rule, not a reuse of any parser written
    for prose: this wants every path, with `_listed` supplying the "and N
    more" tail rather than a hard cap applied before anything can be counted.
    A silently truncated exclusion list has already cost real licensing edits
    landing as though they were agreed.
    """
    scope = session_end._section(text, "Scope")
    cutoff = contract_mod._NOT_CHANGING_RE.search(scope)
    if not cutoff:
        return []
    return [
        line.strip()[2:].strip()
        for line in scope[cutoff.end():].splitlines()
        if line.strip().startswith("- ")
    ]


def _carry_over(session_id: str, root, touched: list[str], blocked: dict) -> str:
    """What the session agreed to, for a session that no longer remembers.

    Every fact comes from a file some other hook already wrote, so this cannot
    disagree with what the gates enforce — it is the same contract the scope
    fence reads and the same shards the Stop gate counts.

    The section helpers come from `session_end`, which already parses a
    contract's headings to harvest its lessons. Restating them here would put a
    second parser on the same hand-editable file, which is exactly how a fix
    lands in one copy and not the other.
    """
    agreed = contract_mod.load(session_id)
    if agreed is None:
        return ""

    text = agreed.text
    goal = session_end._summary(session_end._section(text, "Goal"), GOAL_LIMIT)
    scoped = agreed.scoped_files
    excluded = _excluded(text)
    if not (goal or scoped or excluded):
        return ""  # A contract with nothing in it is not worth a single token.

    # The plan names repo-relative paths; the session records absolute ones.
    # This is only ever used to split a list for display, so unlike the scope
    # fence it can afford to be approximate — the worst case is a file shown as
    # not yet edited when it was.
    prefix = f"{Path(root).as_posix().rstrip('/')}/"
    seen = {p[len(prefix):] for p in (Path(t).as_posix() for t in touched) if p.startswith(prefix)}

    lines = ["", "Carried over from before this session's context was shortened:"]
    if not agreed.approved:
        lines.append(
            f"  This plan is {agreed.status} — **not approved**, so the scope fence is"
            " inert. It is not enforcing the list below."
        )
    if goal:
        lines.append(f"  Goal: {goal}")
    if agreed.verdict and agreed.verdict != "unknown":
        lines.append(f"  Verdict: {agreed.verdict}")

    edited = [entry for entry in scoped if entry in seen]
    remaining = [entry for entry in scoped if entry not in seen]
    if edited:
        lines.append(_listed(edited, "In scope, already edited"))
    if remaining:
        lines.append(_listed(remaining, "In scope, not yet edited"))
    # Never trimmed, and never folded into the two lists above. Real licensing
    # edits have already landed because this list was mis-parsed, so it is the
    # single line least safe to drop when something has to go.
    if excluded:
        lines.append(_listed(excluded, "Agreed NOT to change"))
    if blocked:
        lines.append(
            f"  The end-of-turn gate is currently blocking on: {', '.join(sorted(set(blocked.values())))}."
        )

    lines.append(
        "  This is what you agreed to, not a suggestion. Re-read the files you"
        " need, but do not re-plan work that is already scoped."
    )
    return "\n".join(lines)


def head_commit(root: Path) -> str:
    """The commit this session opens at, for the scope fence to compare against.

    The fence needs a fixed point, and HEAD is not one. A model that edits a
    file the plan never named and commits inside the turn moves HEAD onto its
    own change, so anything comparing against HEAD finds no difference and the
    stray is forgiven — the moving-target hole `stop_gate._vanished` already
    gave up `cat-file -e HEAD:` to escape. Anchoring here costs one `rev-parse`
    per session start and does not move for the rest of the session.

    Empty on an unborn HEAD, outside a repository, or any git refusal. The fence
    reads empty as "no fixed point, cannot prove", and reports.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


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
    # Read before the reset below, never after. `clear` takes the `fresh` branch
    # — the user asked for a clean slate — but it is also a source the snapshot
    # is rendered for, and reading the emptied values told the model every file
    # it had already edited was still outstanding. Which is worse than silence:
    # it is an instruction to redo finished work.
    progress = (session.get("files_touched") or [], session.get("heavy_blocked") or {})
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
        # Which strays have been reported is per-contract, not per-session id,
        # and session ids are reused. Left behind, a path reported under
        # yesterday's plan stays exempt from the fence under today's — the same
        # never-cleared latch this release exists to remove, one field over.
        session["scope_reported"] = []
        session["gave_up_at"] = None
        # Re-anchored with the counters, and for the same reason: a fresh start
        # means the fence judges what happens from here, not what the previous
        # session left in the tree. A resume keeps the commit it already had —
        # its edits are still in flight and still belong to that anchor.
        session["base_commit"] = head_commit(root)
    save_session(session, reset=fresh)

    context = _describe(profile) + _contract_line(event.get("session_id", "unknown"))
    # Lessons live in a hand-editable file, same as the contract, so it will
    # eventually break the renderer. `guard()` would catch that and exit 0,
    # taking the tool profile with it — every session depends on that, and it
    # has nothing to do with lessons — so this degrades to nothing instead.
    try:
        context += _lessons_block(root)
    except Exception:
        pass
    carried = ""
    # Deliberately not gated on `fresh`. `clear` resets the counters — the user
    # asked for a clean slate — but it does not delete the contract, so the
    # scope fence goes on enforcing a plan the model can no longer see. That
    # asymmetry is the whole reason this exists, and it is sharpest here: the
    # model edits a file, the gate blocks it as out of scope, and nothing in the
    # session explains why.
    if event.get("source") in RESUMED_SOURCES:
        # A contract is a file a human edits, so it will eventually be malformed.
        # `guard()` would swallow the traceback, but it would also drop the tool
        # profile every session depends on — so this degrades rather than raises.
        try:
            carried = _carry_over(event.get("session_id", "unknown"), root, *progress)
        except Exception:
            carried = ""
        if carried:
            context = f"{context}\n{carried}"

    trace("SessionStart", event.get("session_id", "?"), "bootstrapped",
          source=event.get("source"), agent=event.get("agent_type"),
          carried=bool(carried))

    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }
    )
    return 0


if __name__ == "__main__":
    with guard("session_start"):
        sys.exit(main())
