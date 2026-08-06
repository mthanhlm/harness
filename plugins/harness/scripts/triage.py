#!/usr/bin/env python3
"""How much argument has this request earned?

The old gate was "past three files or a hundred lines" — a size guess made
before the work starts, which fired in 4% of sessions while 42% of the spend went
to fifteen sessions of a thousand turns each. Size is the wrong axis. The right
one is whether anybody knows what "done" looks like:

    goal clear + verifiable automatically  -> just build it
    anything else                          -> argue about it first

That is Table 5-1 from the book, and the harness's job is to push work into the
top-left quadrant rather than to add ceremony evenly across all four.

**This module does not classify the request.** Deciding whether a goal is clear
is a judgement about language, and a regex that tries it will be confidently
wrong in both directions — waving through a vague request, or demanding a design
debate over a typo, which is how a process gets switched off. What code does well
is count things, so that is all this does: which paths the request names, how
often each has already been rewritten, and whether the repo can check itself
at all.

The evidence forces a route only where counting is enough to force it. Otherwise
it hands the model the readings *and the decision rule* and lets it judge. That
split is deliberate: the book measured a status bar built by a model as worse
than no status bar, and raw readings with no rule attached as barely better than
nothing. Code supplies the reading; the rule travels with it; the model judges.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# A path is worth looking up if it carries a directory or an extension. Bare
# words are not probed: "plan" and "state" are English before they are files, and
# a false hit invents churn evidence for a file the request never mentioned.
_PATH_RE = re.compile(r"\b([\w.\-]+(?:/[\w.\-]+)+|[\w\-]+\.[a-zA-Z][\w]{0,4})\b")

# Rewriting the same mechanism repeatedly is evidence the design is wrong, not
# that this patch is wrong. Three is where the plugin's own plan skill already
# draws that line, so it is drawn in the same place here.
CHURN_ARGUES = 3


@dataclass
class Evidence:
    paths: list[str] = field(default_factory=list)
    churn: dict[str, int] = field(default_factory=dict)
    can_self_check: bool = False
    # Whether churn could be measured at all. Zero commits and no repository to
    # count them in are different readings, and only one of them is evidence.
    has_history: bool = False
    route: str = "undecided"
    because: list[str] = field(default_factory=list)


def _run(args: list[str], root: Path) -> str:
    try:
        proc = subprocess.run(
            args, cwd=str(root), capture_output=True, text=True, timeout=10, check=False
        )
        return proc.stdout if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def named_paths(request: str, root: Path) -> list[str]:
    """Paths the request names that actually exist in the repo.

    Existence is the filter. Without it, `session_end.py` and `foo.bar` are
    equally credible, and churn gets reported for files nobody has.
    """
    seen: list[str] = []
    for candidate in _PATH_RE.findall(request or ""):
        if candidate in seen:
            continue
        if (root / candidate).is_file():
            seen.append(candidate)
            continue
        # A partial path still resolves — people say `scripts/stop_gate.py` for a
        # file that lives at `plugins/harness/scripts/stop_gate.py`, and refusing
        # to look it up means reporting no churn for the file the whole request
        # is about. But only when it resolves to exactly one file: two matches
        # and there is no way to tell which was meant, so neither is claimed.
        found = [f for f in _run(["git", "ls-files", f"*{candidate}"], root).split() if f]
        if len(found) == 1:
            seen.append(found[0])
    return seen


def churn(paths: list[str], root: Path) -> dict[str, int]:
    """How many commits have already touched each path."""
    counts = {}
    for path in paths:
        log = _run(["git", "log", "--oneline", "--", path], root)
        counts[path] = len([line for line in log.splitlines() if line.strip()])
    return counts


def classify(request: str, root: Path) -> Evidence:
    paths = named_paths(request, root)
    ev = Evidence(
        paths=paths,
        churn=churn(paths, root),
        has_history=bool(_run(["git", "rev-parse", "--git-dir"], root).strip()),
    )

    try:
        from detect import build_profile

        # `build_profile` returns the whole profile, not the checks. Iterating it
        # directly walks its keys as strings and every probe silently answers
        # "no" — which reads exactly like a repo that genuinely cannot test
        # itself, so every request routes as manually verified.
        checks = build_profile(root).get("checks", [])
        ev.can_self_check = any(c.get("kind") in ("test", "typecheck") for c in checks)
    except Exception:
        ev.can_self_check = False

    hot = [p for p, n in ev.churn.items() if n >= CHURN_ARGUES]

    # This used to force route C first, ahead of churn, when the roadmap's
    # newest entry touching one of these paths was marked `reworked` — the
    # strongest signal this module had, because it meant a plan of this shape
    # had already been tried and had not held. That signal no longer exists:
    # the roadmap is gone and lessons carry no per-path outcome to force a
    # route on. What is left is the churn heuristic below, on its own.
    if hot:
        ev.route = "B"
        ev.because.append(
            f"{hot[0]} has been rewritten {ev.churn[hot[0]]} times — "
            "a third or later patch to the same mechanism is evidence the design is wrong"
        )
    elif not ev.has_history:
        # Outside a repository every `git log` comes back empty, so the churn
        # counts are all zero and the sentence below would report "the history
        # says nothing" — which reads as "this code is stable" when what actually
        # happened is that nothing was consulted. The strongest evidence this
        # module has is simply unavailable here, and saying so is the difference
        # between a low reading and no reading.
        ev.because.append(
            "this is not a git repository, so there is no churn history to consult —"
            " the usual evidence for arguing is absent, not negative"
        )
    else:
        ev.because.append("nothing in the history argues about this request by itself")

    if not ev.can_self_check:
        ev.because.append("this repo has no test or typecheck the harness can run, so nothing "
                          "here can verify itself — treat the goal as manually verified")
    return ev


def render(ev: Evidence) -> str:
    lines = ["triage:"]
    if ev.paths:
        named = []
        for path in ev.paths:
            n = ev.churn.get(path, 0)
            named.append(f"{path} ({n} commit{'' if n == 1 else 's'})")
        lines.append("  named: " + ", ".join(named))
    else:
        lines.append("  named: no existing file named in the request")
    lines += ["  " + b for b in ev.because]

    if ev.route == "undecided":
        lines += [
            "  route: YOURS TO DECIDE, on one question — does anyone know what done looks like?",
            "    A (build it now)   goal is concrete AND something automated can prove it",
            "    B (challenge it)   one of those two is soft",
            "    C (design it)      the goal is vague, or only a human can say it worked",
        ]
    else:
        lines.append(f"  route: {ev.route} — forced by the evidence above, not a judgement call")
    return "\n".join(lines)


def main() -> int:
    from state import repo_root

    argv = sys.argv[1:]
    # The flag is taken out of the request rather than compared against the whole
    # of it. `request == "--json"` meant the flag only worked when it was the only
    # argument — so `triage.py --json "<request>"` printed the human rendering and
    # fed the literal string `--json` into the path scan, and the caller parsing
    # the result got neither the JSON it asked for nor any sign it had not.
    as_json = "--json" in argv
    request = " ".join(a for a in argv if a != "--json")
    root = repo_root()
    ev = classify(request, root)
    print(json.dumps(asdict(ev)) if as_json else render(ev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
