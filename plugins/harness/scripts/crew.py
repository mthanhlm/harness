#!/usr/bin/env python3
"""Work out which specialists a job needs.

The point of a crew is that one job usually needs several kinds of expertise at
once — a paginated endpoint backed by a new query and rendered in a component is
a database job, a backend job and a frontend job simultaneously. Picking one
specialist for it loses two thirds of the review.

The split here is deliberate, and it took a wrong turn first.

**Paths are facts**, so they are matched mechanically: `db/schema.ts` is a
database file whatever anyone thinks, and a mechanical answer is reproducible
and can be shown to the user to disagree with.

**What a task is about is judgement**, so this file does not attempt it. It used
to, with a keyword list, and keyword lists cannot work: matching was by
substring, so `ui` fired inside "b*ui*ld", `api` inside "c*api*tal", and `auth`
inside "*auth*or". Tightening to whole words only narrows the guess — the real
problem is that a fixed vocabulary can never cover how people phrase things, and
at plan time, when nothing has changed yet, that guess carries the entire
selection.

So the catalogue of lenses goes out with the report and whoever reads it picks
the ones the job needs. That is the same basis Claude Code already loads skills
on, rather than a second, worse mechanism competing with it.
"""

from __future__ import annotations

import fnmatch
import json
import re
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


def select_lenses(files: list[str]) -> list[dict[str, Any]]:
    """The lenses the changed files put beyond argument."""
    selected = []
    for lens in registry()["lenses"]:
        matched = [f for f in files if any(_matches_path(p, f) for p in lens["paths"])]
        if matched:
            selected.append(
                {
                    "name": lens["name"],
                    "domain": lens["domain"],
                    "because": f"{len(matched)} matching file(s), e.g. {matched[0]}",
                }
            )
    return selected


def lens_catalogue() -> list[dict[str, str]]:
    """Every lens and what it covers, for whoever is judging the task itself."""
    return [{"name": l["name"], "domain": l["domain"]} for l in registry()["lenses"]]


# Agents ship inside a plugin, so the Task tool addresses them by a scoped name.
# The registry stores the bare name; passing that straight to `subagent_type`
# fails with an unknown-agent error, which reads like the model not bothering.
AGENT_PREFIX = "harness:"


def select_roles(phase: str) -> list[dict[str, Any]]:
    return [
        {**role, "subagent_type": f"{AGENT_PREFIX}{role['name']}"}
        for role in registry()["roles"]
        if role["phase"] == phase
    ]


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else "after"
    task = " ".join(sys.argv[2:])

    from state import repo_root

    root = repo_root()
    files = changed_files(root)
    roles = select_roles(phase)

    report = {
        "task": task,
        "changed_files": files,
        "lenses_from_files": select_lenses(files),
        "lens_catalogue": lens_catalogue(),
        "roles_always": [r for r in roles if r.get("always")],
        "roles_conditional": [r for r in roles if not r.get("always")],
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
