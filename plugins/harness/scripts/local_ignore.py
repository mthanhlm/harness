#!/usr/bin/env python3
"""Keep the harness's own artifacts out of the user's repository.

`.codegraph/` and `.harness/` belong to the tooling, not to the product being
shipped, and neither should ever appear in `git status` or a commit.

The entries go in `.git/info/exclude` rather than `.gitignore`, and that choice
is the whole point of this file. `.gitignore` is a **tracked** file: a hook
editing it puts a change in the user's next commit that the user did not write,
which is exactly the pollution being avoided. `.git/info/exclude` is local,
untracked, and appears in no diff.

The cost is that it is per clone, so a teammate has to get their own. That is the
right trade for a tool's own scratch directories, and the wrong one for anything
the project genuinely needs ignored — which is why this only ever writes these
two entries and never becomes a general mechanism.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ENTRIES = (".codegraph/", ".harness/")

HEADER = "# added by the harness plugin — local only, not part of the repository"


def _exclude_file(root: Path) -> Path | None:
    """Where this clone keeps its local excludes, or None outside a repo.

    Asked of git rather than assumed to be `.git/info/exclude`, because in a
    worktree or a submodule `.git` is a file and the real directory is elsewhere.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-path", "info/exclude"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    path = Path(proc.stdout.strip())
    return path if path.is_absolute() else root / path


def ensure(root: Path) -> list[str]:
    """Add any missing entries. Returns what it added, empty when already done."""
    path = _exclude_file(root)
    if path is None:
        return []

    try:
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return []

    present = {line.strip() for line in existing.splitlines()}
    missing = [e for e in ENTRIES if e not in present and e.rstrip("/") not in present]
    if not missing:
        return []

    block = "" if existing.endswith("\n") or not existing else "\n"
    block += f"{HEADER}\n" + "".join(f"{entry}\n" for entry in missing)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(block)
    except OSError:
        return []
    return missing


def main() -> int:
    from state import repo_root

    added = ensure(repo_root())
    print(f"local ignore: added {', '.join(added)}" if added else "local ignore: already set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
