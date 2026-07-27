#!/usr/bin/env python3
"""UserPromptSubmit hook: nudge toward a contract when work looks substantial.

A skill description alone leaves the decision to the model's judgement in the
moment, which is exactly when it is most inclined to just start typing. A short
reminder at the point the request arrives costs almost nothing and lands before
any of that momentum builds.

The classifier is deliberately crude — regex over the prompt, no model call. A
model call here would add latency and cost to every single prompt in order to
decide whether to suggest something the model is free to ignore anyway. The
nudge is advisory; being approximately right is enough.
"""

from __future__ import annotations

import re
import sys

import contract as contract_mod
from state import emit, gates_disabled, guard, read_event, session_state

# At most twice. A reminder that arrives every turn stops being read, and the
# model has other, stronger signals pointing at the same skill.
MAX_NUDGES = 2

_BUILD_RE = re.compile(
    r"\b(implement|build|create|add|write|refactor|rewrite|migrate|port|"
    r"integrate|replace|extract|introduce|support|wire up|hook up|set up|"
    r"redesign|restructure|convert)\b",
    re.IGNORECASE,
)

# Asking about code is not the same as changing it, and the contract skill has
# nothing useful to say about a question.
_ASK_RE = re.compile(
    r"^\s*(what|why|how|where|when|who|which|is|are|does|do|did|can|could|"
    r"should|would|explain|describe|show|list|find|search|read|review|check|"
    r"summari[sz]e|tell me|walk me)\b",
    re.IGNORECASE,
)

# Phrases that mark a change as small enough that ceremony would be an insult.
_SMALL_RE = re.compile(
    r"\b(typo|rename|one[- ]liner|bump|log line|comment|whitespace|format|"
    r"lint|import|version)\b",
    re.IGNORECASE,
)

NUDGE = (
    "This looks like implementation work. Before editing, use the `plan` skill"
    " to agree scope, check what already exists, and give a"
    " patch/refactor-first/rewrite verdict — then wait for approval."
    " If the change is genuinely one or two lines, skip it and just do the work."
)


def _wants_building(prompt: str) -> bool:
    if not prompt.strip() or prompt.strip().startswith("/"):
        return False
    if _ASK_RE.match(prompt) or prompt.rstrip().endswith("?"):
        return False
    if _SMALL_RE.search(prompt) and len(prompt) < 200:
        return False
    return bool(_BUILD_RE.search(prompt))


def main() -> int:
    if gates_disabled():
        return 0

    event = read_event()
    prompt = event.get("prompt")
    if not isinstance(prompt, str) or not _wants_building(prompt):
        return 0

    session_id = event.get("session_id", "unknown")
    existing = contract_mod.load(session_id)
    if existing is not None:
        return 0  # A contract exists; the reminder has already done its job.

    with session_state(session_id) as session:
        nudges = int(session.get("contract_nudges") or 0)
        if nudges >= MAX_NUDGES:
            return 0
        session["contract_nudges"] = nudges + 1

    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": NUDGE,
            }
        }
    )
    return 0


if __name__ == "__main__":
    with guard("prompt_gate"):
        sys.exit(main())
