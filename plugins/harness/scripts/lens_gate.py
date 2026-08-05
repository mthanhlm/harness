#!/usr/bin/env python3
"""Did the reviewer actually have domain knowledge in front of it?

This is the enforcement half of a design that spent a while being the wrong
shape, so the reasoning is worth writing down.

Selecting a lens started as path matching, which is a proxy: a filename
correlates with a domain and does not determine one. `src/checkout/handler.ts`
builds SQL from a request body and matches no security pattern by name;
`internal/store.go` running a migration matched nothing at all and was reviewed
with no domain knowledge whatsoever. The obvious fix — regexes over the diff —
is a second proxy with a second set of misses, and a set of patterns somebody
then maintains forever.

What can actually tell that `store.go` is running a migration is the agent
holding the diff. So the agent selects, and this hook is what makes that a
requirement rather than a suggestion — the previous version of "the agent
selects" was an instruction in a brief, and it was skipped exactly as often as
instructions are.

`SubagentStop` is the only point in a subagent's life that can block, and a
block there does the right thing: the agent does not stop, it keeps working, and
it is told why.

Two bounds, because a blocking hook that gets it wrong is worse than no hook:

- **Once per agent.** The transcript is written asynchronously and may lag the
  conversation, so a read that happened at the very end might not be visible
  yet. One block costs a cycle; blocking on a stale read forever costs the task.
- **Reviewers only.** The worker builds to a brief and the refuter attacks one
  claim; neither produces a domain judgement whose quality silently depends on a
  lens. A gate that fires on everything gets switched off.
"""

from __future__ import annotations

import json
from pathlib import Path

from state import plugin_root

# Agents whose entire output is a domain judgement. A review written without the
# relevant lens has the same shape, length and confidence as one written with
# it, so nothing downstream can tell — which is what makes this worth a gate
# rather than a note.
GATED = ("reviewer-",)

LENS_DIR = "references/lenses/"

# The plugin name from `.claude-plugin/plugin.json`, which is what Claude Code
# scopes this plugin's agent types with. Kept beside `AGENT_PREFIX` in `crew`,
# which spells the same fact with the colon attached.
PLUGIN_SCOPE = "harness"


def is_gated(agent: str) -> bool:
    return any(agent.startswith(prefix) for prefix in GATED)


def bare_name(agent_type: str) -> str:
    """This plugin's own agents by their bare name; everyone else's as-is.

    `SubagentStop` fires for **every** subagent in the session, not only ours,
    and a plugin agent's `agent_type` is scoped: `harness:reviewer-security`,
    `otherplugin:reviewer-security`. Splitting on the colon and keeping the tail
    made those two the same agent — so a second plugin shipping a reviewer would
    have its agents blocked, mid-task, by a message about lenses it has never
    heard of and a directory belonging to a plugin it is not part of.

    Unscoped names are treated as ours. Built-ins (`Explore`, `general-purpose`)
    arrive unscoped and match no gate anyway, and a plugin loaded with
    `--plugin-dir` during development is the case that would otherwise stop being
    checked exactly while it is being worked on.
    """
    name = str(agent_type or "").strip()
    if ":" not in name:
        return name
    scope, _, tail = name.partition(":")
    return tail.strip() if scope.strip() == PLUGIN_SCOPE else name


def agent_transcript(event: dict) -> str | None:
    """The subagent's *own* transcript, which is not the one named `transcript_path`.

    A `SubagentStop` payload carries both. `transcript_path` is the main
    session's file; the subagent's tool calls are in `agent_transcript_path`,
    under a nested `subagents/` directory. This gate was reading the first, where
    an agent's Read of a lens page can never appear — so `lenses_read` returned
    the empty set no matter what the agent had done, and the only thing keeping
    a compliant reviewer from being blocked was that something had been injected.

    Falls back to `transcript_path` rather than to nothing: if a future payload
    drops the field, reading the wrong file is no worse than the behaviour this
    replaces, and reading none would silently re-arm the same false block.
    """
    for key in ("agent_transcript_path", "transcript_path"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def lenses_read(transcript_path: str | None) -> set[str]:
    """Lens pages this agent opened, read from its own transcript.

    A tool call is a fact about what happened, unlike anything inferred from the
    agent's prose — an agent that says "applying the security lens" without
    having opened it leaves no Read behind, and that is the case this exists to
    catch.
    """
    if not transcript_path:
        return set()
    try:
        raw = Path(transcript_path).read_text(encoding="utf-8")
    except OSError:
        return set()

    found: set[str] = set()
    for line in raw.splitlines():
        # Cheap prefilter: the vast majority of transcript lines cannot match,
        # and parsing every one of them is the difference between a hook that
        # costs nothing and one somebody notices.
        if LENS_DIR not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        for path in _read_paths(event):
            if LENS_DIR in path:
                found.add(Path(path).stem)
    return found


def _read_paths(event: object) -> list[str]:
    """Every `file_path` a Read-like tool call in this event names.

    Walks rather than indexing a known shape. Transcript entries are a private
    format that has changed before, and a hook that silently finds nothing when
    the shape shifts would report every reviewer as having skipped its lens.
    """
    out: list[str] = []
    stack = [event]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            value = node.get("file_path")
            if isinstance(value, str):
                out.append(value)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


def verdict(agent: str, injected: list[str], read: set[str]) -> str | None:
    """The reason to block, or None to let it stop.

    Having a lens injected counts. The point is not to make agents perform a
    Read; it is that no domain judgement gets made with nothing in front of it.
    """
    if not is_gated(agent):
        return None
    if injected or read:
        return None
    # An absolute directory, resolved here. This block reason is hook *output*,
    # and Claude Code substitutes `${CLAUDE_PLUGIN_ROOT}` only into skill and
    # agent content and into hook commands — so the previous wording blocked an
    # agent for not reading a page and then named that page with a placeholder
    # the agent had no way to expand.
    directory = f"{plugin_root() / LENS_DIR}/"
    return (
        "You are about to report a review that had no domain knowledge behind it.\n\n"
        "No lens matched the paths in this change, and you did not read one. That "
        "is a statement about the filenames, not about the change — a handler that "
        "builds SQL, a Go file that runs a migration and a service that chunks "
        "documents all match nothing by name.\n\n"
        "Read the diff, decide which lenses it is actually about, and read them "
        f"from `{directory}<name>.md` — the catalogue, with the full path of every "
        "page, was listed when you started. Then redo the review with them in "
        "front of you.\n\n"
        "If you genuinely believe no lens applies, say so in your report and name "
        "the two you considered. That is an answer; silence is not."
    )
