#!/usr/bin/env python3
"""PostCompact hook: put the agreed plan back after the context is compacted.

Compaction is where a long session quietly stops being bound by its plan. The
contract was approved near the start; after compaction, what survives is a
summary of the conversation, and a summary keeps the narrative and drops the
constraints — the file fence, the budget, the thing the plan promised not to
touch. Nothing errors. The work simply carries on against a plan nobody is
holding any more.

That matters here more than it would elsewhere, because long sessions are
exactly where the cost is. Sessions past 200 turns run 133 lines changed per file
touched against 10 for short ones: the same files, rewritten, because the design
drifted while it was being built. Compaction sits in the middle of every one of
those sessions.

So the fix is the smallest one available: re-inject the parts of the contract a
summary loses, and nothing else. Not the whole contract — the sections that are
constraints. Goal and Data flow are narrative and the summary keeps their sense;
Scope, Budget and the exclusion list are load-bearing and get dropped.

Deliberately silent when there is no approved contract. Most sessions do not
have one, and a hook that lectures on every compaction is a hook that gets
switched off.
"""

from __future__ import annotations

import re
import sys

import contract as contract_mod
from state import emit, gates_disabled, guard, read_event

# What a summary keeps and what it drops. Scope is the fence the end-of-turn
# gate enforces, Budget is what the outcome is judged against, and Verification
# is the command that decides whether any of this worked.
CARRY = ("Scope", "Budget", "Verification", "Prediction")
MAX_CHARS = 4000


def _section(text: str, heading: str) -> str:
    """Delegated to `contract.section`, which was the third copy of this.

    This one required the heading line to be exactly `## Scope`, so a plan that
    wrote `## Scope (files this will change)` carried no Scope through the
    compaction — and if every heading in `CARRY` is qualified that way,
    `reminder` returns None and the hook stays silent, which is indistinguishable
    from a session with no approved plan. The compaction that this file exists to
    survive is then the one that unbinds the session from its fence.
    """
    return contract_mod.section(text, heading).strip()


def reminder(agreed: contract_mod.Contract) -> str | None:
    """The constraints the summary just lost, or None if there are none."""
    text = agreed.text
    title = re.search(r"^#\s*Plan:\s*(.+)$", text, re.MULTILINE)

    parts = []
    for heading in CARRY:
        body = _section(text, heading)
        if body:
            parts.append(f"## {heading}\n{body}")
    if not parts:
        return None

    header = title.group(1).strip() if title else "the approved plan"
    out = "\n\n".join(parts)
    if len(out) > MAX_CHARS:
        # Truncate at a section boundary rather than mid-list: half a file fence
        # is worse than one section fewer, because it reads as the whole fence.
        kept: list[str] = []
        for part in parts:
            if sum(len(k) for k in kept) + len(part) > MAX_CHARS:
                break
            kept.append(part)
        out = "\n\n".join(kept or parts[:1])

    return (
        "<approved_plan>\n"
        f"The context was just compacted. This session is working to an approved "
        f"plan — **{header}** — and these are the parts of it a summary drops.\n\n"
        f"{out}\n\n"
        "The Scope list is the fence the end-of-turn gate checks against; editing "
        "outside it is reported. If the work has genuinely outgrown this plan, say "
        "so and get the change agreed rather than quietly exceeding it — a plan "
        "executed at twice its budget is recorded as `reworked`.\n"
        "</approved_plan>"
    )


def main() -> int:
    if gates_disabled():
        return 0

    event = read_event()
    agreed = contract_mod.load(event.get("session_id", "unknown"))
    if not agreed or not agreed.approved:
        return 0  # No plan to drift from. Say nothing.

    text = reminder(agreed)
    if not text:
        return 0

    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostCompact",
                "additionalContext": text,
            }
        }
    )
    return 0


if __name__ == "__main__":
    with guard("post_compact"):
        sys.exit(main())
