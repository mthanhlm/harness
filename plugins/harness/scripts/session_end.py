#!/usr/bin/env python3
"""SessionEnd hook: write one line to the ledger about what the session cost,
and harvest any lessons the session's plan says it learned.

Recording happens at the end rather than per turn because that is when the
transcript is complete and the counters have stopped moving. A session that
changed nothing is skipped — a ledger full of conversational turns hides the
sessions that actually did work.

Lessons are harvested rather than asked for. This plugin's own history is
that the plan skill telling the model to write one at the end of the turn
never once worked across a full day of real use — it was the last instruction
in a long skill, competing with the report, and by then the answer was already
in hand. So a hook takes it instead, out of a `## Lessons` section the plan
wrote *before* the work, when attention was on it. Most sessions teach nothing
durable; a contract with no such section writes nothing, which is correct.
"""

from __future__ import annotations

import re
import sys

import contract as contract_mod
import lessons
from ledger import record
from state import gates_disabled, guard, load_session, read_event, repo_root


def _section(text: str, heading: str) -> str:
    """The body under one `## ` heading, up to the next one.

    Delegated rather than restated. This file's own copy required the heading
    line to be exactly `## Verdict`, so a plan writing `## Verdict — patch` got
    an empty section — and when every section comes back empty, the harvest
    below finds nothing to write. `session_start` reaches for this same
    function for its own carry-over, and a second copy of the parser is how a
    fix lands in one of them and not the other.
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


def _lessons_bullets(text: str) -> list[str]:
    """The bullets under a plan's `## Lessons` section, joined across
    continuation lines.

    Contracts are hard-wrapped at about eighty columns, so reading one
    physical line per bullet would cut a lesson off mid-clause — the same
    failure the old roadmap's deferred-bullet parser had to be fixed for.
    """
    section = _section(text, "Lessons")
    if not section:
        return []

    # The unfilled template placeholder. Every section in the skill's contract
    # template is described inside angle brackets, and a plan that left one in
    # place said nothing — harvesting it would file the instructions for writing
    # a lesson as a lesson. Skipped as a block, not a line: contracts are
    # hard-wrapped, so only the first line of a placeholder opens with `<` and
    # dropping just that one leaves the rest of the instructions looking like
    # prose somebody wrote on purpose.
    lines: list[str] = []
    in_placeholder = False
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if in_placeholder:
            in_placeholder = not stripped.endswith(">")
            continue
        if stripped.startswith("<"):
            in_placeholder = not stripped.endswith(">")
            continue
        lines.append(stripped)

    # Whether a bare line continues the bullet above it or is a lesson of its
    # own cannot be read off the line, so it is decided once for the section.
    # This mattered: the skill says "one line each" and its own two examples
    # carry no dash, so a plan that followed the instruction exactly produced no
    # bullets at all — `write_lessons` returned early inside a caller that
    # swallows exceptions, and the result was indistinguishable from a session
    # that had learned nothing. Being generous here is not the same trade as in
    # the scope fence, where matching prose as a path accuses files nobody named:
    # a stray lesson is one somebody can revise, a missed one is memory gone with
    # no record that it ever existed.
    dashed = any(line.startswith("- ") for line in lines)

    bullets: list[list[str]] = []
    for line in lines:
        if not dashed:
            bullets.append([line])
        elif line.startswith("- "):
            bullets.append([line[2:].strip()])
        elif bullets:
            bullets[-1].append(line)
    joined = [" ".join(" ".join(parts).split()) for parts in bullets]
    return [b for b in joined if b]


_SUPERSEDES_RE = re.compile(r"^supersedes[ \t]+(L\d+)[ \t]*[:—-][ \t]*(.+)$", re.IGNORECASE)


def _split_lesson(bullet: str) -> tuple[str, str, str | None]:
    """A bullet's title, its elaboration, and the lesson it retires if any.

    Written as `Title: elaboration.` — the same shape a plan already uses for
    its Verdict line (`patch — the architect read the code...`). A bullet with
    no colon has nothing to split, so the whole sentence stands as both.

    A bullet opening `supersedes L3: ...` corrects that lesson instead of adding
    a new one. The declaration sits on the bullet rather than in a header at the
    top of the contract, where it used to live: a header names which entry dies
    but not which text replaces it, so the pairing had to be reconstructed by
    hand, by a model, at the end of a long turn — and this plugin's own record is
    that such a step is never taken. Here the two halves cannot be separated,
    because they are the same sentence.
    """
    if superseding := _SUPERSEDES_RE.match(bullet):
        retires, rest = superseding.group(1).upper(), superseding.group(2).strip()
        title, body, _ = _split_lesson(rest)
        return title, body, retires
    if ": " in bullet:
        title, body = bullet.split(": ", 1)
        return title.strip(), body.strip(), None
    return bullet.strip(), bullet.strip(), None


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
    not the same as fine, and recording it as `held` would put a fact on record
    that nothing established.

    Called only for an approved contract. An unapproved plan forecast nothing
    anyone agreed to, so judging a session against it would put a verdict on
    record that no decision backs.
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


def write_lessons(session: dict) -> None:
    """Harvest the session's `## Lessons` bullets into durable entries, once.

    A repeated SessionEnd for one session must not stack duplicates, so a
    lesson already on file is left alone — the same guard the old roadmap
    needed for the same reason. Any failure here is swallowed: notes are worth
    less than the ledger line that follows them.

    Identity is the title *and* the body, not the title alone. `_split_lesson`
    cuts at the first `": "`, so two genuinely different lessons that open with
    the same clause — easy, since a lesson's opening clause names the thing it
    is about — collapsed to one, and the survivor read as the complete harvest.
    """
    agreed = contract_mod.load(session.get("session_id", "unknown"))
    if not agreed or not agreed.approved:
        return
    bullets = _lessons_bullets(agreed.text)
    if not bullets:
        return
    root = repo_root(session.get("repo_root"))
    on_file = {(lesson.title, lesson.body) for lesson in lessons.entries(root)}
    for bullet in bullets:
        title, body, retires = _split_lesson(bullet)
        if not title or (title, body) in on_file:
            continue
        if retires:
            try:
                lessons.revise(retires, title, body, root)
            except ValueError:
                # The plan named a lesson that is not on file — a typo, or a
                # lesson pruned long ago. Recording it as new loses the claim
                # that something was corrected, but recording nothing loses the
                # lesson itself, and the lesson is the part that cannot be
                # reconstructed from anywhere else.
                lessons.append(title, body, root)
        else:
            lessons.append(title, body, root)
        on_file.add((title, body))


def main() -> int:
    if gates_disabled():
        return 0

    event = read_event()
    session = load_session(event.get("session_id", "unknown"))
    if not session.get("files_touched"):
        return 0

    try:
        write_lessons(session)
    except Exception:
        pass  # Notes must never be the reason the cost record is lost.

    agreed = contract_mod.load(session.get("session_id", "unknown"))
    outcome = outcome_for(agreed, session) if agreed and agreed.approved else None
    record(session, event.get("transcript_path"), outcome=outcome)
    return 0


if __name__ == "__main__":
    with guard("session_end"):
        sys.exit(main())
