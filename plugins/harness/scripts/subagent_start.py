#!/usr/bin/env python3
"""SubagentStart hook: hand each agent the domain knowledge its job needs.

This replaces an instruction. The agents used to be told to run `lenses.py`
themselves, which has two failure modes and both are silent: the agent skips the
step, or it passes the wrong paths. A reviewer with no lens still produces a
review — same shape, same length, same confidence — so nothing downstream can
tell the difference.

Selecting a lens is arithmetic over paths, and arithmetic belongs in code. The
book measures this directly: a status bar maintained by code beat one maintained
by a frontier model summarising its own history, and the model-maintained
version scored *below* having no status bar at all.

Which paths depends on when the agent runs, and there is no third case:

    before the code exists (challenger, designer)
      → there is no diff. Their subject is the request itself, so they get the
        requirements lens and the catalogue of what else is available.
    after the code exists (reviewers, refuter, worker)
      → the changed files are a fact. Route on them.

Failures here are swallowed. This runs on the way into somebody else's task, and
an agent that does not start is worse than one that starts without its lens.
"""

from __future__ import annotations

import sys

import lens_gate
from crew import changed_files, lens_catalogue
from lenses import lens_path, bodies
from state import (
    emit,
    gates_disabled,
    guard,
    read_event,
    repo_root,
    shard_update,
    writer_id,
)

# Agents that run before there is a diff to look at. Their subject is the
# request, so paths cannot select for them and a fixed lens is correct.
PRE_CODE = ("challenger", "designer")
PRE_CODE_LENSES = ("lens-requirements",)

# Where the agent's own subject is one lens, whatever the paths say. A security
# review is usually of a route handler rather than anything under `auth/`, and a
# test review reads the implementation as often as the test.
SUBJECT_LENS = {
    "reviewer-security": ("lens-security",),
    "reviewer-tests": ("lens-testing",),
}

# Enough to be useful, bounded so a wide diff cannot fill the agent's context
# with domains it will not use. Four lenses is already every layer of a
# full-stack change.
MAX_LENSES = 4


def agent_name(event: dict) -> str:
    """The bare agent name, from whatever scoped form arrives.

    Plugin agents are addressed as `harness:reviewer-perf`, and the matcher in
    `hooks.json` is what selects them — but the event carries the scoped name
    and every lookup here is by the bare one.

    Shared with `subagent_stop` rather than restated, because the two have to
    agree: this decides which lenses go in, that one decides whether enough went
    in, and a name they disambiguate differently is a gate firing on an agent
    that was never given anything.
    """
    return lens_gate.bare_name(event.get("agent_type"))


def selection(name: str, root) -> tuple[list[str], list[str]]:
    """The lenses to name outright, and the paths to take a head start from."""
    if name in PRE_CODE:
        return list(PRE_CODE_LENSES), []
    return list(SUBJECT_LENS.get(name, ())), changed_files(root)


def _catalogue_block(loaded: list[str]) -> str:
    """Everything not already loaded, and the instruction to go and get it.

    Sent every time, not only when nothing matched — which is the shape this had
    and it was the wrong way round. A total miss is rare and self-announcing; the
    common failure is a *partial* miss, where two lenses load, they look like the
    answer, and nothing tells the agent a third exists.

    This block is the primary mechanism now, not the fallback. Paths above are a
    head start; **you** are the one holding the diff, and reading it is the only
    way anybody finds out that a file called `store.go` is running a migration.
    `subagent_stop.py` checks that this happened, which is what stops it being
    one more instruction that gets skipped.
    """
    rest = [l for l in lens_catalogue() if l["name"] not in loaded]
    if not rest:
        return ""
    # A resolved directory, once, and the names under it. Claude Code substitutes
    # `${CLAUDE_PLUGIN_ROOT}` into skill and agent *content* and into hook
    # *commands* — never into what a hook prints. So the earlier version of this
    # block handed every agent a placeholder to expand itself, from a shell that
    # defines no such variable, and across every transcript on this machine not
    # one agent has ever opened a lens page: the catalogue was the whole
    # mechanism and it addressed nothing.
    #
    # Said once rather than per lens. This block goes into every agent's context,
    # and fourteen absolute paths say the same thing as one directory at fourteen
    # times the length — which is the crowding that makes the important line get
    # skimmed past.
    directory = lens_path("<name>").parent
    listed = "\n".join(f"  {l['name']:<20} {l['domain']}" for l in rest)
    return (
        "\nNow read the change and decide which of these it is *actually* about.\n"
        "Paths are a weak proxy for domain — a checkout handler builds SQL, a file\n"
        "named `store.go` runs migrations, and neither says so in its name.\n"
        f"Read any that apply from {directory}/<name>.md before you start:\n\n"
        f"{listed}\n"
    )


def context_for(name: str, root) -> tuple[str, list[str]]:
    """The block to inject, and the lens names it contains.

    The names go back to the caller rather than being recomputed at stop time.
    `changed_files` moves while the agent works, so re-deriving the selection
    later answers a different question from the one that was actually asked.
    """
    named, files = selection(name, root)
    found = bodies(named, files)[:MAX_LENSES]
    loaded = [lens for lens, _ in found]

    if not found:
        return (
            "<domain_knowledge>\nNo path in this change matched a lens, which is a"
            " statement about the filenames and not about the change.\n"
            + _catalogue_block([]) + "</domain_knowledge>"
        ), []

    parts = [
        "<domain_knowledge>",
        "Loaded from the paths in this change. Apply it; do not restate it back.",
        "This is a head start, not the selection — see the list at the end.",
        "",
    ]
    for lens, text in found:
        parts += [f'<lens name="{lens}">', text.rstrip(), "</lens>", ""]
    parts.append(_catalogue_block(loaded))
    parts.append("</domain_knowledge>")
    return "\n".join(parts), loaded


def main() -> int:
    if gates_disabled():
        return 0

    event = read_event()
    name = agent_name(event)
    if not name:
        return 0

    text, loaded = context_for(name, repo_root())

    # Recorded so `lens_gate` can ask what this agent was actually given rather
    # than re-deriving it from a diff that has moved on since.
    #
    # Through `shard_update` rather than a bare read-then-write, because the
    # shard is the same file `session_state` accumulates into and a read-modify-
    # write without the lock is exactly how those updates get lost — the module
    # docstring on `_exclusive` counts 471 of 600 increments dropped.
    with shard_update(event.get("session_id", "unknown"), writer_id(event)) as shard:
        shard["lenses_injected"] = loaded

    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": text,
            }
        }
    )
    return 0


if __name__ == "__main__":
    with guard("subagent_start"):
        sys.exit(main())
