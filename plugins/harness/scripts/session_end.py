#!/usr/bin/env python3
"""SessionEnd hook: write one line to the ledger about what the session cost,
and one entry to the roadmap about what it decided.

Recording happens at the end rather than per turn because that is when the
transcript is complete and the counters have stopped moving. A session that
changed nothing is skipped — a ledger full of conversational turns hides the
sessions that actually did work.

The roadmap entry is derived rather than asked for. The plan skill has always
told the model to write one at the end of the turn, and across a full day of
real use it never once did: it is the last instruction in a long skill, competing
with the report, and by then the answer is already in hand. So it is taken from
the contract instead — Verdict, Disagreement and what the plan refused to touch
were all written *before* the work, when attention was on them, and they are a
better record than anything reconstructed afterwards.
"""

from __future__ import annotations

import re
import sys

import contract as contract_mod
import roadmap
from ledger import record
from state import gates_disabled, guard, load_session, read_event, repo_root

MAX_DEFERRED = 4


def _section(text: str, heading: str) -> str:
    """The body under one `## ` heading, up to the next one.

    Delegated rather than restated. This file's own copy required the heading
    line to be exactly `## Verdict`, so a plan writing `## Verdict — patch` got an
    empty section — and when every section comes back empty, `entry_for` returns
    None and the session's decisions are never written to the roadmap at all.
    The comment in `_not_changing` below already says why a second copy of a
    parser is how a fix lands in one of them and not the other.
    """
    return contract_mod.section(text, heading).strip()


def _summary(body: str, limit: int = 200) -> str:
    """The opening of a section, ending on a sentence rather than mid-clause.

    A contract is hard-wrapped at about eighty columns, so its first *line* is a
    fragment: taken verbatim it ends the entry in the middle of a word. Join the
    paragraph, then cut at the last sentence boundary that fits.
    """
    for block in body.split("\n\n"):
        text = " ".join(" ".join(line.strip().lstrip("-").strip() for line in block.splitlines()).split())
        if not text:
            continue
        if len(text) <= limit:
            return text
        cut = text[:limit]
        stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        return cut[: stop + 1] if stop > 0 else cut.rstrip() + "…"
    return ""


def _not_changing(text: str) -> list[str]:
    """The bullets under `Explicitly NOT changing` inside `## Scope`.

    This is the closest thing a plan has to a list of known, deliberate
    omissions — work that was seen, judged real, and left. That is exactly what
    the next session would otherwise rediscover.
    """
    scope = _section(text, "Scope")
    # The same pattern the scope fence cuts on, imported rather than restated.
    # It was written out twice, and the second copy is exactly how a fix lands
    # in one parser and not the other — which is the bug that produced it.
    cutoff = contract_mod._NOT_CHANGING_RE.search(scope)
    if not cutoff:
        return []

    # Bullets are joined across continuation lines before being returned.
    # Contracts are hard-wrapped at about eighty columns, so reading one
    # physical line per bullet cut ten of twenty-seven real entries mid-clause —
    # `- deferred: detect.py —` tells the next session nothing, which is the
    # only thing this file exists to prevent.
    bullets: list[list[str]] = []
    for line in scope[cutoff.end():].splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            bullets.append([stripped[2:].strip()])
        elif stripped and bullets:
            bullets[-1].append(stripped)
        elif not stripped:
            continue
    joined = [" ".join(" ".join(parts).split()) for parts in bullets]
    return [b for b in joined if b][:MAX_DEFERRED]


def entry_for(agreed: contract_mod.Contract) -> tuple[str, str] | None:
    """A title and body for the roadmap, or None if there is nothing to say."""
    text = agreed.text
    heading = re.search(r"^#\s*Plan:\s*(.+)$", text, re.MULTILINE)
    title = heading.group(1).strip() if heading else "unnamed plan"

    lines = []
    verdict_why = _summary(_section(text, "Verdict"))
    if verdict_why:
        lines.append(f"- decided: {agreed.verdict} — {verdict_why}")

    disagreement = _summary(_section(text, "Disagreement"))
    if disagreement and disagreement.rstrip(".").lower() not in ("none", "n/a"):
        lines.append(f"- decided: {disagreement}")

    lines += [f"- deferred: {item}" for item in _not_changing(text)]

    if not lines:
        return None
    return title, "\n".join(lines)


def outcome_for(agreed: contract_mod.Contract, session: dict) -> str:
    """Whether the plan held, judged against its own forecast.

    A plan that predicts four files and a hundred lines, and whose session ends
    having rewritten nine files at four hundred lines apiece, was not executed —
    it was redesigned while being built. That is the rework this whole flow
    exists to make visible, and it is the one outcome signal available here that
    does not require asking a model what it thinks happened.

    Deliberately narrower than it could be. Whether the contract was amended
    after approval, and whether the verification command ever ran and passed,
    would both be better signals; neither is recorded anywhere today, and
    inventing them from the transcript would be a guess wearing a label.

    `open` is the honest answer for a plan that wrote no Budget: unmeasured is
    not the same as fine, and recording it as `held` would put a fact in the
    roadmap that nothing established.
    """
    budget = agreed.budget
    if not budget:
        return "open"
    want_files, want_lines = budget
    got_files = len(session.get("files_touched") or [])
    got_lines = int(session.get("lines_changed") or 0)
    if not got_files:
        return "open"

    # Two ways to overrun: touching files the plan never anticipated, or
    # rewriting the anticipated ones far more heavily than it thought. The
    # second is the churn signature; the first is scope drift.
    if want_files and got_files >= 2 * max(want_files, 1):
        return "reworked"
    if want_lines and got_lines >= 2 * max(want_lines, 1):
        return "reworked"
    return "held"


def write_roadmap(session: dict) -> None:
    """Append the session's decisions, once, and never at the cost of the hook.

    A repeated SessionEnd for one session must not stack duplicates, so an entry
    whose title is already present is left alone. Any failure here is swallowed:
    notes are worth less than the ledger line that follows them.
    """
    agreed = contract_mod.load(session.get("session_id", "unknown"))
    if not agreed or not agreed.approved:
        return
    derived = entry_for(agreed)
    if not derived:
        return
    title, body = derived
    root = repo_root(session.get("repo_root"))
    # Compared against entry *titles*, not against the file as a whole. A
    # substring test over the whole roadmap silently dropped any plan whose title
    # happened to appear anywhere in it — including inside the body of an older
    # entry that merely *mentioned* the same subject, which is the normal case
    # for follow-up work on the same area. The session that did the follow-up
    # then recorded nothing, and the roadmap looked like it had already covered
    # ground it had only talked about.
    if any(entry.title == title for entry in roadmap.entries(root)):
        return
    roadmap.append(
        root,
        title,
        body,
        supersedes=agreed.supersedes,
        outcome=outcome_for(agreed, session),
    )


def main() -> int:
    if gates_disabled():
        return 0

    event = read_event()
    session = load_session(event.get("session_id", "unknown"))
    if not session.get("files_touched"):
        return 0

    try:
        write_roadmap(session)
    except Exception:
        pass  # Notes must never be the reason the cost record is lost.

    record(session, event.get("transcript_path"))
    return 0


if __name__ == "__main__":
    with guard("session_end"):
        sys.exit(main())
