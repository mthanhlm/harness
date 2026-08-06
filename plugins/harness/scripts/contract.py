#!/usr/bin/env python3
"""Reading the session's contract.

The contract is a markdown file the model writes and the user approves. The
gates only need three facts from it — whether it exists, whether it is approved,
and which files it fenced — so parsing stays deliberately shallow. A stricter
format would be more precise and would also make the model fight the syntax
instead of thinking about the design.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from state import contracts_dir

# The environment variable Claude Code exports to Bash tool calls, verified
# against a running session rather than assumed. `${CLAUDE_SESSION_ID}` — which
# three skills used to name — is not a variable at all and is not one of the
# three placeholders Claude Code substitutes, so it arrived at the shell
# verbatim: 225 occurrences of a literal `contracts/${CLAUDE_SESSION_ID}.md`
# across real transcripts, each one a `cat` of a file that cannot exist.
SESSION_ID_ENV = "CLAUDE_CODE_SESSION_ID"

_STATUS_RE = re.compile(r"^\s*status:\s*(\w[\w -]*)", re.MULTILINE | re.IGNORECASE)
_VERDICT_RE = re.compile(r"^\s*verdict:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
# Scope entries are markdown bullets naming a path, optionally followed by a
# dash and a description: "- src/auth/session.ts — refresh handling".
#
# Two alternatives, because requiring an extension was the only thing keeping
# prose bullets ("- Files this will change:") out of the fence — and it also
# excluded `Dockerfile`, `Makefile` and `.env`, so a plan that scoped one had its
# own file reported as a stray. Backticks say "this is a path" unambiguously, so
# a backticked entry needs no extension and an unbackticked one still does.
_SCOPE_ENTRY_RE = re.compile(
    r"^[ \t]*[-*][ \t]+(?:`([\w./\-]+)`|([\w./\-]+\.[\w]+)\b)",
    re.MULTILINE,
)


def section(text: str, heading: str) -> str:
    """The body under one markdown heading, up to the next one.

    One implementation, because there were two and they disagreed. This one
    required a *following* `## ` heading, so a contract whose Scope was its last
    section parsed as zero files; the copy in `session_end` handled that and
    instead required the heading line to be exactly `## Scope`, so a plan that
    wrote `## Scope (files this will change)` parsed as zero there. Each file had
    fixed the bug the other still had.

    Both failures are silent and both are expensive. An empty scope list makes
    `_out_of_scope` return nothing, so the end-of-turn gate certifies every edit
    in the session as agreed; an empty section in `session_end` drops the
    lessons the session earned, so what it learned is never recorded at all.

    Deliberately generous, for the reason `_NOT_CHANGING_RE` below is: matching
    too much narrows the section, which is noisy, and matching too little empties
    it, which is silent.
    """
    pattern = rf"^#{{1,6}}[ \t]*{re.escape(heading)}\b[^\n]*\n(.*?)(?=^#{{1,6}}[ \t]|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


# Where the agreed list stops and the deliberately-excluded list begins.
#
# Anchored to the start of a line, because an unanchored search for "not chang"
# also matches a plan that merely *mentions* the section in a bullet: that
# truncated the fence at the bullet and quietly dropped every agreed file after
# it, and one real plan fenced 5 of its 17 files that way.
#
# But the anchor has to accept what markdown actually produces, and the first
# version accepted only two exact spellings. `### Explicitly NOT changing`,
# `Not changing:` and `*Explicitly NOT changing*:` all failed to match, so the
# excluded bullets were parsed as *in scope* — the gate then certified edits to
# the very files the plan promised not to touch. Both mistakes are silent; this
# one is the dangerous direction, so the prefix is deliberately generous.
#
# `[ \t]` rather than `\s`: under MULTILINE, `\s*` re-consumes whole runs of
# blank lines at every retry, which is quadratic on a long contract.
_NOT_CHANGING_RE = re.compile(
    r"^[ \t]*(?:[#>]{1,6}[ \t]*)?(?:[*_]{1,3}[ \t]*)?"
    r"(?:Explicitly[ \t]+|Deliberately[ \t]+)?NOT[ \t]+chang\w*",
    re.MULTILINE | re.IGNORECASE,
)
# "~4 files, ~120 lines." — tolerant of the tilde, of "file"/"line", and of
# whatever the model puts between the two numbers.
_BUDGET_RE = re.compile(r"~?\s*(\d+)\s*files?\b.*?~?\s*(\d+)\s*lines?\b", re.IGNORECASE | re.DOTALL)
# A plan used to name what it reversed in a `Supersedes:` header, read from
# here and handed to the roadmap. It is gone with the roadmap, and not because
# supersession stopped mattering: a header three hundred lines above the lesson
# it retires says which entry dies but never which text replaces it, so the
# pairing had to be reconstructed by hand and never was. It is declared on the
# `## Lessons` bullet itself now — see `session_end._split_lesson`.


def contract_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in session_id) or "unknown"
    return contracts_dir() / f"{safe}.md"


class Contract:
    def __init__(self, text: str) -> None:
        self.text = text

    @property
    def status(self) -> str:
        match = _STATUS_RE.search(self.text)
        return match.group(1).strip().lower() if match else "pending"

    @property
    def approved(self) -> bool:
        return self.status == "approved"

    @property
    def verdict(self) -> str:
        match = _VERDICT_RE.search(self.text)
        return match.group(1).strip().lower() if match else "unknown"

    @property
    def budget(self) -> tuple[int, int] | None:
        """The `~N files, ~N lines` the plan predicted, if it wrote one.

        This is the plan's own forecast, made before the work and while attention
        was on it. Comparing it against what the session actually changed is the
        only outcome signal available at SessionEnd that does not require asking
        a model what it thinks happened.
        """
        match = _BUDGET_RE.search(self.text)
        return (int(match.group(1)), int(match.group(2))) if match else None

    @property
    def scoped_files(self) -> list[str]:
        """Paths listed under `## Scope`, up to the next heading.

        Only the "will change" list is collected; the "NOT changing" list sits
        under the same heading and is deliberately not treated as scope.
        """
        body = section(self.text, "Scope")
        if not body:
            return []
        cutoff = _NOT_CHANGING_RE.search(body)
        if cutoff:
            body = body[: cutoff.start()]
        return [backticked or bare for backticked, bare in _SCOPE_ENTRY_RE.findall(body)]


def load(session_id: str) -> Contract | None:
    path = contract_path(session_id)
    try:
        return Contract(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def main(argv: list[str]) -> int:
    """`contract.py path [session-id]` — where this session's contract belongs.

    Prints one absolute path and nothing else, so it composes: `cat "$(… path)"`.

    Refuses rather than guesses. With no session id and no `CLAUDE_CODE_SESSION_ID`
    there is no way to name the right file, and the two available guesses are both
    worse than an error — a made-up name writes a contract no gate will ever read,
    and the newest file in the directory belongs to whichever session was last,
    which on a second terminal is somebody else's plan.
    """
    if not argv or argv[0] != "path":
        print("usage: contract.py path [session-id]", file=sys.stderr)
        return 2

    session_id = argv[1] if len(argv) > 1 else os.environ.get(SESSION_ID_ENV, "")
    if not session_id.strip():
        print(
            f"contract: no session id — ${SESSION_ID_ENV} is unset and none was"
            " given. The harness printed the exact path at session start; use that,"
            " or pass the id: contract.py path <session-id>",
            file=sys.stderr,
        )
        return 1

    print(contract_path(session_id.strip()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
