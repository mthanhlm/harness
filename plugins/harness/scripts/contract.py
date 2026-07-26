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
        cutoff = re.search(r"not\s+chang", body, re.IGNORECASE)
        if cutoff:
            body = body[: cutoff.start()]
        return _SCOPE_ENTRY_RE.findall(body)


def load(session_id: str) -> Contract | None:
    path = contract_path(session_id)
    try:
        return Contract(path.read_text(encoding="utf-8"))
    except OSError:
        return None
