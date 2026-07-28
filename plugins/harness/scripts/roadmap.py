#!/usr/bin/env python3
"""What this project decided, and what it left undone.

Every session starts from nothing. Work gets done, a real problem gets found and
reported, and the next session has never heard of it — so the same ground is
re-covered and the same deferred item is deferred again. That is the difference
between a tool and a colleague, and it is a file, not a feature.

Two deliberate limits, because an append-only log rots into a wall nobody reads:

- **Decisions and deferred work only.** Not a session narrative. If it does not
  change what someone would do next, it does not go in.
- **A hard cap.** Old entries fall off. A roadmap nobody can read to the end is
  the same as no roadmap, and the recent decisions are the ones still in force.

It lives in the repository so it survives a plugin reinstall and moves with the
code — and is excluded locally, because it belongs to the tooling rather than to
the product being shipped.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

MAX_ENTRIES = 25

PREAMBLE = """# Roadmap

Decisions taken and work deliberately deferred, newest first. Written by
`/harness:plan`; edit or delete anything here freely — it is notes, not state.
"""


def roadmap_path(root: Path) -> Path:
    return root / ".harness" / "roadmap.md"


def read(root: Path) -> str:
    try:
        return roadmap_path(root).read_text(encoding="utf-8")
    except OSError:
        return ""


def _entries(text: str) -> list[str]:
    """Split into `## ` sections, newest first.

    Anchored to the start of a line *and* tolerant of the file beginning with a
    heading, because the header invites editing it by hand — and the first
    version silently ate the newest entry of anyone who deleted the preamble.
    """
    import re

    marks = [m.start() for m in re.finditer(r"^## ", text, re.MULTILINE)]
    return [text[a:b].rstrip() for a, b in zip(marks, marks[1:] + [len(text)])]


def append(root: Path, title: str, body: str) -> Path:
    """Add one dated entry at the top and trim the tail."""
    existing = _entries(read(root))
    entry = f"## {date.today().isoformat()} — {title.strip()}\n\n{body.strip()}\n"
    kept = [entry] + existing[: MAX_ENTRIES - 1]

    path = roadmap_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PREAMBLE + "\n" + "\n\n".join(kept) + "\n", encoding="utf-8")
    return path


def main() -> int:
    from state import repo_root

    root = repo_root()
    action = sys.argv[1] if len(sys.argv) > 1 else "show"

    if action == "show":
        text = read(root)
        print(text if text.strip() else "roadmap: nothing recorded for this project yet")
        return 0

    if action == "append":
        title = " ".join(sys.argv[2:]).strip() or "untitled"
        body = sys.stdin.read().strip()
        if not body:
            print("roadmap: nothing to record (body was empty)")
            return 0
        print(f"roadmap: recorded under {date.today().isoformat()} — {title}")
        append(root, title, body)
        return 0

    print(f"roadmap: unknown action {action!r} (use show or append)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
