#!/usr/bin/env python3
"""Work out which specialists a job needs.

The point of a crew is that one job usually needs several kinds of expertise at
once — a paginated endpoint backed by a new query and rendered in a component is
a database job, a backend job and a frontend job simultaneously. Picking one
specialist for it loses two thirds of the review.

The split here is deliberate, and it took two wrong turns first.

**Which specialists a phase runs is a fact about the registry**, so `select_roles`
answers it mechanically and gives the same answer every time.

**What a change is about is judgement**, so this file does not decide it. It tried
twice. First with a keyword list over the task text, which cannot work: matching
was by substring, so `ui` fired inside "b*ui*ld", `api` inside "c*api*tal", and
`auth` inside "*auth*or" — and tightening to whole words only narrows the guess,
because no fixed vocabulary covers how people phrase things.

Then with path globs, on the claim that a path *is* a fact. It is not the fact
that matters. `db/schema.ts` really is a database file, but
`src/checkout/handler.ts` building SQL from a request body matches no database or
security pattern by name, and every miss of that kind was silent — the review ran
with no domain knowledge and said nothing about it. Each miss invited one more
pattern, which is the patchwork this plugin exists to argue against.

What survives here is only what is genuinely mechanical: a `.py` file is Python
whoever is asking. Everything past that is a suggestion. The thing that can
actually read a diff and say what it is about is the agent holding it, so the
agent chooses from the catalogue — see `subagent_start.py` for how the catalogue
reaches it, and `subagent_stop.py` for the gate that refuses a review conducted
with no domain knowledge at all.
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
    # "**/evals/**" should match "evals/cases/a.py" — a directory at the repo
    # root has nothing before it for the leading `**/` to consume, so both of
    # the branches above miss it. Two lenses already declared patterns in this
    # shape and neither had ever fired on a top-level directory.
    if pattern.startswith("**/") and pattern.endswith("/**"):
        middle = pattern[3:-3]
        if path.startswith(middle + "/") or f"/{middle}/" in path:
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
    """The lenses these paths suggest — a head start, not the selection.

    This used to be the whole mechanism and it should not have been. A path
    correlates with a domain; it does not determine one. `src/checkout/handler.ts`
    builds SQL from a request body and matches no security or database pattern by
    name, and `internal/store.go` running a migration matched nothing at all — so
    it was reviewed with no domain knowledge and nothing said so.

    Matching harder does not fix that. The next brittle proxy has the next miss,
    and the misses are silent every time. What actually reads a diff and knows
    what it is about is the agent holding it, so the agent picks — see
    `subagent_start.py` for the catalogue it picks from and `subagent_stop.py`
    for the check that it did.

    What survives here is the part that is genuinely a fact rather than a guess:
    a `.py` file is Python whoever is asking. Those load for free and save the
    agent a round trip. Everything else this returns is a suggestion that the
    agent is free to ignore and expected to add to.
    """
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
    """Every lens and what it covers.

    No longer part of this report. Each agent loads its own domain knowledge
    from the paths it is handed — `scripts/lenses.py`, which is where this is
    now read from — so shipping the catalogue here made the lead choose lenses
    on behalf of agents that had already chosen better.
    """
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
        "roles_always": [r for r in roles if r.get("always")],
        "roles_conditional": [r for r in roles if not r.get("always")],
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
