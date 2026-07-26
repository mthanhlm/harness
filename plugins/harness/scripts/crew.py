#!/usr/bin/env python3
"""Work out which specialists a job needs.

The point of a crew is that one job usually needs several kinds of expertise at
once — a paginated endpoint backed by a new query and rendered in a component is
a database job, a backend job and a frontend job simultaneously. Picking one
specialist for it loses two thirds of the review.

Matching is mechanical rather than a judgement call, for two reasons: it is
reproducible, and it can be shown to the user, who can then disagree with a
concrete list instead of guessing what was considered.

Files are the stronger signal and are matched first. The task description is
used as a fallback so the crew can be assembled before anything has been written.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from state import plugin_root


def registry() -> dict[str, Any]:
    path = plugin_root() / "crew" / "registry.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _matches_path(pattern: str, path: str) -> bool:
    """Glob match that also accepts a `**/` pattern against a bare filename."""
    if fnmatch.fnmatch(path, pattern):
        return True
    if pattern.startswith("**/") and fnmatch.fnmatch(Path(path).name, pattern[3:]):
        return True
    # "app/**" should match "app/page.tsx" as well as deeper paths.
    if pattern.endswith("/**") and (path.startswith(pattern[:-3] + "/") or f"/{pattern[:-3]}/" in path):
        return True
    return False


def changed_files(root: Path) -> list[str]:
    """Files touched since HEAD, including untracked ones."""
    files: list[str] = []
    for args in (["diff", "--name-only", "HEAD"], ["ls-files", "--others", "--exclude-standard"]):
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            files.extend(line for line in proc.stdout.splitlines() if line.strip())
    return sorted(set(files))


def select_lenses(files: list[str], task: str) -> list[dict[str, Any]]:
    reg = registry()
    lowered = task.lower()
    selected = []
    for lens in reg["lenses"]:
        by_path = [f for f in files if any(_matches_path(p, f) for p in lens["paths"])]
        by_word = [k for k in lens["keywords"] if k in lowered]
        if by_path or by_word:
            selected.append(
                {
                    "name": lens["name"],
                    "domain": lens["domain"],
                    "because": (
                        f"{len(by_path)} matching file(s), e.g. {by_path[0]}"
                        if by_path
                        else f"task mentions {', '.join(by_word[:3])}"
                    ),
                }
            )
    return selected


def select_roles(phase: str) -> list[dict[str, Any]]:
    return [r for r in registry()["roles"] if r["phase"] == phase]


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else "after"
    task = " ".join(sys.argv[2:])

    from state import repo_root

    root = repo_root()
    files = changed_files(root)
    lenses = select_lenses(files, task)
    roles = select_roles(phase)

    report = {
        "changed_files": files,
        "lenses": lenses,
        "roles_always": [r for r in roles if r.get("always")],
        "roles_conditional": [r for r in roles if not r.get("always")],
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
