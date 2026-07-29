#!/usr/bin/env python3
"""Reading the session's contract.

The contract is a markdown file the model writes and the user approves. The
gates only need three facts from it — whether it exists, whether it is approved,
and which files it fenced — so parsing stays deliberately shallow. A stricter
format would be more precise and would also make the model fight the syntax
instead of thinking about the design.
"""

from __future__ import annotations

import re
from pathlib import Path

from state import contracts_dir

_STATUS_RE = re.compile(r"^\s*status:\s*(\w[\w -]*)", re.MULTILINE | re.IGNORECASE)
_VERDICT_RE = re.compile(r"^\s*verdict:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
# Scope entries are markdown bullets naming a path, optionally followed by a
# dash and a description: "- src/auth/session.ts — refresh handling".
_SCOPE_ENTRY_RE = re.compile(r"^\s*[-*]\s+`?([\w./\-]+\.[\w]+)`?", re.MULTILINE)
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
    def scoped_files(self) -> list[str]:
        """Paths listed under `## Scope`, up to the next heading.

        Only the "will change" list is collected; the "NOT changing" list sits
        under the same heading and is deliberately not treated as scope.
        """
        section = re.search(r"^##\s*Scope\s*$(.*?)^##\s", self.text, re.MULTILINE | re.DOTALL)
        if not section:
            return []
        body = section.group(1)
        cutoff = _NOT_CHANGING_RE.search(body)
        if cutoff:
            body = body[: cutoff.start()]
        return _SCOPE_ENTRY_RE.findall(body)


def load(session_id: str) -> Contract | None:
    path = contract_path(session_id)
    try:
        return Contract(path.read_text(encoding="utf-8"))
    except OSError:
        return None
