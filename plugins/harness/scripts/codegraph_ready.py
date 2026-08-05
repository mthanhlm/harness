#!/usr/bin/env python3
"""Make sure this repo has a CodeGraph index, building one if it does not.

The reuse audit is the check that stops the same helper being written twice, and
it is markedly better when it can follow calls instead of matching text. Grep
finds `formatName`; CodeGraph finds the `toDisplay` that already does the job.

Indexing is per repository — a global install does nothing for a fresh clone —
so this runs `codegraph init` on first use rather than leaving the audit to
degrade quietly. It is idempotent and cheap to call before every audit.

Prints one line for the caller to relay, and never fails the caller: a missing
binary or a failed index just means the grep fallback applies.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

from state import repo_root

# Indexing is usually seconds. A repo big enough to exceed this is one where the
# user should decide for themselves rather than have a skill sit on it.
INIT_TIMEOUT = 300


def main() -> int:
    root = repo_root(sys.argv[1] if len(sys.argv) > 1 else None)

    if (root / ".codegraph").is_dir():
        print(f"codegraph: indexed ({root})")
        return 0

    if not shutil.which("codegraph"):
        print("codegraph: not installed — using grep and glob instead")
        return 0

    if not (root / ".git").exists():
        # Outside a repo the index has no stable home and would land wherever
        # the shell happened to be. Not worth the surprise.
        print(f"codegraph: {root} is not a git repository — using grep and glob instead")
        return 0

    print(f"codegraph: no index in {root}, building one…")
    try:
        proc = subprocess.run(
            ["codegraph", "init"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=INIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"codegraph: init could not run ({exc}) — using grep and glob instead")
        return 0

    if proc.returncode != 0 or not (root / ".codegraph").is_dir():
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else "no output"
        print(f"codegraph: init failed ({tail}) — using grep and glob instead")
        return 0

    summary = [ln.strip() for ln in (proc.stdout or "").splitlines() if "nodes" in ln]
    print(f"codegraph: indexed {root}" + (f" — {summary[-1]}" if summary else ""))

    # What actually happened, rather than a chore that is both unnecessary and
    # contrary to the plugin's own design. This used to tell the user to add
    # `.codegraph/` to `.gitignore` — a *tracked* file, which is precisely the
    # pollution `local_ignore.py` exists to avoid, and its docstring says so.
    # `local_ignore.ensure` has already excluded it locally at session start, so
    # the advice was asking for a commit nobody needed and one the rest of the
    # plugin was written to prevent.
    import local_ignore

    local_ignore.ensure(root)  # idempotent; session start has normally done it already
    if local_ignore.excluded(root, ".codegraph/"):
        print("codegraph: excluded locally via .git/info/exclude — nothing to commit")
    else:
        print("codegraph: could not exclude `.codegraph/` locally — add it to .gitignore yourself")
    return 0


if __name__ == "__main__":
    sys.exit(main())
