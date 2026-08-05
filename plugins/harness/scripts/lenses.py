#!/usr/bin/env python3
"""Print the domain knowledge that the files in front of you actually call for.

These were nine skills. Every agent that might need one declared six of them in
its frontmatter, which meant six names in the model-facing listing for each of
several agents, and the *selection* — which of the six this particular job needs
— was left to a model reading its own frontmatter.

Knowledge is not a skill. A skill is instructions with tools and a model
attached; these are pages to read. Being read as information is what they already
were, and declaring them as skills only advertised them.

What this does **not** do is decide which ones a job needs. That was tried, with
path globs in `crew/registry.json`, and the misses were silent: a handler that
builds SQL from a request body matches no database or security pattern by name,
so the review ran with no domain knowledge and nothing said so. The agent holding
the diff is what can answer that, so it names the lenses it wants and this prints
them. The path matches that still come back are a free head start, not a
selection — see `crew.select_lenses`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from crew import lens_catalogue, registry, select_lenses
from state import plugin_root


def lens_path(name: str) -> Path:
    return plugin_root() / "references" / "lenses" / f"{name}.md"


def split_args(args: list[str]) -> tuple[list[str], list[str]]:
    """Arguments that name a lens, and arguments that name a file.

    Some agents need a lens whatever the paths say. The security reviewer's
    whole subject is `lens-security`, and its diff is usually a route handler
    rather than anything under `auth/` — matching on paths alone would have
    silently dropped the one lens it cannot work without. Naming it explicitly
    is how that stays true, and it composes with the path matching rather than
    replacing it.
    """
    known = {lens["name"] for lens in registry()["lenses"]}
    return [a for a in args if a in known], [a for a in args if a not in known]


def bodies(names: list[str], files: list[str]) -> list[tuple[str, str]]:
    """The lenses named outright, plus the ones these paths mechanically suggest.

    A lens named in the registry with no file behind it is skipped rather than
    raised on: this runs inside an agent that is trying to do something else,
    and a stack trace there costs the whole task to save a page of advice. The
    wiring test is what catches the missing file, at the point it can be fixed.
    """
    found = select_lenses(files)
    wanted = list(names) + [l["name"] for l in found if l["name"] not in names]
    out = []
    for name in wanted:
        try:
            out.append((name, lens_path(name).read_text(encoding="utf-8")))
        except OSError:
            continue
    return out


def catalogue() -> str:
    """Every lens and what it covers — for choosing by subject when there are no
    paths yet, which is the situation at plan time."""
    lines = ["No paths given. The full catalogue, to pick from by subject:", ""]
    for lens in lens_catalogue():
        lines.append(f"  {lens['name']:<18} {lens['domain']}")
    lines += ["", "Re-run with the files you are about to work on to load the matching ones."]
    return "\n".join(lines)


def main() -> int:
    args = [a for a in sys.argv[1:] if a.strip()]
    if not args:
        print(catalogue())
        return 0

    names, files = split_args(args)
    found = bodies(names, files)
    if not found:
        print("No lens matches those paths — nothing domain-specific to load.")
        return 0

    for name, text in found:
        print(f"===== {name} =====\n{text.rstrip()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
