#!/usr/bin/env python3
"""What this project has learned, and kept learning about.

Its predecessor was a roadmap of decisions and deferred work, and the user's
own verdict on it was exact: it changed nothing, because a plan almost never
reads notes about *open* work before starting more work. What did change
behaviour, provably, in this plugin's own history, was the opposite shape — a
past decision that stopped a regex being widened, one that gave the correct
two-command release sequence, one that stopped `git checkout` being used as a
mutation restore, one that stopped a rejected proposal being re-proposed. Every
one of those is a closed, durable fact: something the project now knows that it
did not know before. Every miss the old file caused was a `deferred:` bullet —
a claim about work still open, which is exactly the kind of claim that rots the
moment the work finishes or the plan changes.

So this file keeps only the first kind. One entry per durable lesson, nothing
about outcomes, budgets or what a plan predicted for itself — that belongs to
the ledger, which measures it instead of asking a model to say it.

The learning has to be able to update, or a wrong lesson is worse than no
lesson: it looks exactly as authoritative as a right one. `revise` is that
mechanism, and its whole point is that being wrong stays visible. It never
rewrites or deletes the old entry — it appends a new one that names what it
replaces, and marks the old one in place as superseded. Both stay in the file
and both are returned by `entries()`; only `render()`'s index treats them
differently, the same way a live decision and a reversed one read differently
to a person opening the file by hand.

It lives in the repository so it survives a plugin reinstall and moves with the
code, and is excluded locally, because it belongs to the tooling rather than to
the product being shipped.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

# A pressure valve, not a bound, and not a context budget — `render(limit=...)`
# is what protects the context a session actually pays for. Above this, superseded
# entries are dropped, oldest first, because whatever they said has already been
# answered by the entry that replaced them. A live lesson is never dropped and
# neither is a hand-written note, so a project with enough of either to exceed
# this keeps every one of them and the file goes over. That is the right way
# round: the cap exists so an automatic writer cannot fill a disk, not so the
# file can be trimmed to fit by deleting things somebody meant to keep.
MAX_BYTES = 250_000

PREAMBLE = """# Lessons

Durable things this project has learned, newest first. `revise <id>` when one
turns out to be wrong — it stays in the file, marked superseded, rather than
being rewritten or deleted.
"""

# `## L4 · 2026-08-06 · Releasing this plugin takes two commands`. The
# separator is a middle dot rather than an em-dash so it cannot be confused
# with the date-then-dash heading the old roadmap used, in case both files are
# ever open at once.
_HEAD_RE = re.compile(r"^##[ \t]+(L\d+)[ \t]*·[ \t]*(\d{4}-\d{2}-\d{2})[ \t]*·[ \t]*(.*)$")
# The metadata line sits directly under the heading, no blank line between them
# — that is what makes it "under the heading" rather than the start of the
# body, and is the one thing a hand edit is most likely to disturb.
_META_LINE_RE = re.compile(r"^(supersedes|superseded-by):[ \t]*(\S+)$", re.IGNORECASE)
_ID_RE = re.compile(r"^L(\d+)$")


@dataclass(frozen=True)
class Lesson:
    id: str
    date: str
    title: str
    body: str
    supersedes: str | None
    superseded_by: str | None


def _resolve(root: str | Path | None) -> Path:
    if root is not None:
        return Path(root)
    from state import repo_root

    return repo_root()


def lessons_path(root: str | Path | None = None) -> Path:
    return _resolve(root) / ".harness" / "lessons.md"


def read(root: str | Path | None = None) -> str:
    try:
        return lessons_path(root).read_text(encoding="utf-8")
    except OSError:
        return ""


def _split(text: str) -> list[str]:
    """Raw `## ` sections, newest first.

    Anchored to the start of a line so a file beginning with a heading is not
    mistaken for one with a missing preamble.
    """
    marks = [m.start() for m in re.finditer(r"^## ", text, re.MULTILINE)]
    return [text[a:b].rstrip() for a, b in zip(marks, marks[1:] + [len(text)])]


def _demote_headings(body: str) -> str:
    """`## ` inside a lesson body becomes `### `.

    `_split` cuts the file on `^## `, so a body containing one would be read as
    the start of the next lesson — the tail would fail to parse, and the next
    write would drop it. The old roadmap did exactly this demotion for exactly
    this reason, and the test that pinned it was deleted along with the module.
    A heading one level deeper says the same thing to a reader and cannot be
    mistaken for a record boundary.
    """
    return re.sub(r"^##([ \t])", r"###\1", body, flags=re.MULTILINE)


def _parse(chunk: str) -> Lesson | None:
    """One lesson, or `None` when the chunk does not match the contract's
    shape. A hand edit that breaks the heading loses tracking of that one
    entry rather than taking down every entry after it in the file."""
    lines = chunk.splitlines()
    if not lines:
        return None
    head = _HEAD_RE.match(lines[0])
    if not head:
        return None
    lesson_id, lesson_date, title = head.group(1), head.group(2), head.group(3).strip()
    if not title:
        return None

    supersedes: str | None = None
    superseded_by: str | None = None
    consumed = 0
    for line in lines[1:]:
        meta = _META_LINE_RE.match(line.strip())
        if not meta:
            break
        key, value = meta.group(1).lower(), meta.group(2)
        if key == "supersedes":
            supersedes = value
        else:
            superseded_by = value
        consumed += 1

    body = "\n".join(lines[1 + consumed :]).strip()
    return Lesson(
        id=lesson_id, date=lesson_date, title=title, body=body,
        supersedes=supersedes, superseded_by=superseded_by,
    )


def _items(root: str | Path | None = None) -> list[Lesson | str]:
    """Everything in the file: parsed lessons, and unparsed chunks kept verbatim.

    The unparsed ones are the whole point. `_write` rebuilds the file from this
    list, so anything missing from it is deleted from disk — and the first
    version built that list out of `entries()` alone. A note somebody typed into
    `.harness/lessons.md` by hand survived every read, because `entries` merely
    skips what it cannot parse, and then vanished on the next automatic append.

    The file's own preamble invites hand editing. Losing what a person wrote,
    silently, on a write they did not make, is the worst thing this module could
    do — worse than any lesson it fails to record.
    """
    try:
        return [_parse(chunk) or chunk for chunk in _split(read(_resolve(root)))]
    except Exception:
        return []


def entries(root: str | Path | None = None) -> list[Lesson]:
    """Every lesson on file, newest first, live and superseded alike.

    Degrades to `[]` rather than raising: the file invites hand editing, and a
    caller that cannot open it is in exactly the position of a project with no
    lessons yet — that is the safe direction to fail in.
    """
    return [item for item in _items(root) if isinstance(item, Lesson)]


def _next_id(existing: list[Lesson]) -> str:
    """Monotonic across the whole file, superseded entries included — an id
    once given out is never handed to a second lesson, or `supersedes: L2`
    could end up pointing at the wrong entry after enough churn."""
    used = [int(m.group(1)) for e in existing if (m := _ID_RE.match(e.id))]
    return f"L{max(used, default=0) + 1}"


def _render(lesson: Lesson) -> str:
    meta = ""
    if lesson.supersedes:
        meta += f"supersedes: {lesson.supersedes}\n"
    if lesson.superseded_by:
        meta += f"superseded-by: {lesson.superseded_by}\n"
    head = f"## {lesson.id} · {lesson.date} · {lesson.title}"
    return f"{head}\n{meta}\n{lesson.body.strip()}\n"


def _text_of(item: Lesson | str) -> str:
    return _render(item) if isinstance(item, Lesson) else item.rstrip() + "\n"


def _prune(kept: list[Lesson | str]) -> list[Lesson | str]:
    """Drop superseded entries, oldest first, while the file is over `MAX_BYTES`.

    A live lesson is the whole reason this file exists and is never on the
    table, and neither is a chunk somebody hand-wrote: this cannot tell a note
    that was abandoned from one that matters, and guessing wrong deletes writing
    nobody can get back.

    So it stops rather than forcing the file under the cap, and `MAX_BYTES` is a
    pressure valve, not a bound — a project with enough live lessons to exceed it
    keeps every one of them. That is the right way round. The cap exists so an
    automatic writer cannot fill a disk; the context a session actually pays for
    is bounded separately and unconditionally by `render(limit=...)`.
    """
    while len(kept) > 1 and sum(len(_text_of(e)) for e in kept) > MAX_BYTES:
        retired = [
            i for i, e in enumerate(kept) if isinstance(e, Lesson) and e.superseded_by
        ]
        if not retired:
            break
        kept.pop(retired[-1])
    return kept


def _write(root: Path, kept: list[Lesson | str]) -> Path:
    """Replace the file atomically.

    A `SessionEnd` killed at the 1.5 second budget every hook on the machine
    shares would otherwise leave `lessons.md` truncated — and the next append,
    reading that truncation as the file, would make it permanent.

    Deliberately does not take the lock. `append` and `revise` hold it across
    their whole read-modify-write, which is the span that has to be serialised,
    and `flock` taken twice in one process on two handles of the same file
    blocks on itself.
    """
    import os
    import tempfile

    path = lessons_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(_text_of(e).rstrip() for e in kept)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False, suffix=".tmp"
    )
    try:
        with handle:
            handle.write(PREAMBLE + "\n" + body + "\n")
        os.replace(handle.name, path)
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return path


def append(title: str, body: str, root: str | Path | None = None) -> Lesson:
    """Add one dated lesson at the top. Never touches an existing entry — use
    `revise` when one turns out to be wrong."""
    resolved = _resolve(root)
    from state import _exclusive

    with _exclusive(lessons_path(resolved)):
        items = _items(resolved)
        lesson = Lesson(
            id=_next_id([e for e in items if isinstance(e, Lesson)]),
            date=date.today().isoformat(),
            title=title.strip() or "untitled",
            body=_demote_headings(body.strip()),
            supersedes=None,
            superseded_by=None,
        )
        _write(resolved, _prune([lesson] + items))
    return lesson


def revise(lesson_id: str, title: str, body: str, root: str | Path | None = None) -> Lesson:
    """Correct a lesson without erasing that it was ever believed.

    Appends a new lesson carrying `supersedes: <lesson_id>` and stamps
    `superseded-by: <new-id>` onto the old one in place. The old title and
    body are never rewritten — only a fresh copy of the old entry, with one
    field changed, replaces it in the written list.
    """
    resolved = _resolve(root)
    from state import _exclusive

    with _exclusive(lessons_path(resolved)):
        items = _items(resolved)
        lessons_on_file = [e for e in items if isinstance(e, Lesson)]
        if not any(e.id == lesson_id for e in lessons_on_file):
            raise ValueError(f"no lesson {lesson_id!r} on file")

        new_lesson = Lesson(
            id=_next_id(lessons_on_file),
            date=date.today().isoformat(),
            title=title.strip() or "untitled",
            body=_demote_headings(body.strip()),
            supersedes=lesson_id,
            superseded_by=None,
        )
        updated: list[Lesson | str] = [
            replace(e, superseded_by=new_lesson.id)
            if isinstance(e, Lesson) and e.id == lesson_id
            else e
            for e in items
        ]
        _write(resolved, _prune([new_lesson] + updated))
    return new_lesson


def show(root: str | Path | None, lesson_id: str) -> str:
    for lesson in entries(root):
        if lesson.id == lesson_id:
            note = f"\nSUPERSEDED by {lesson.superseded_by}." if lesson.superseded_by else ""
            was = f"\nSupersedes {lesson.supersedes}." if lesson.supersedes else ""
            return f"## {lesson.id} · {lesson.date} · {lesson.title}{note}{was}\n\n{lesson.body}"
    return f"lessons: no entry {lesson_id!r}"


def render(root: str | Path | None = None, limit: int | None = None) -> str:
    """A plain-text index — one line per lesson, live and superseded both.

    `limit`, when given, is a hard character cap on the rendered text. This
    file lives in the repository, so a clone's copy belongs to whoever wrote
    the repo, and one hostile or merely huge entry must not be able to flood a
    session's opening context with it.
    """
    found = entries(root)
    if not found:
        text = "lessons: nothing recorded for this project yet"
    else:
        lines = ["Lessons learned in this repo. `lessons.py show <id>` for one in full."]
        for lesson in found:
            mark = f"  (superseded by {lesson.superseded_by})" if lesson.superseded_by else ""
            lines.append(f"{lesson.id:>4}  {lesson.date:10}  {lesson.title}{mark}")
        text = "\n".join(lines)

    if limit is not None and len(text) > limit:
        text = text[: limit].rstrip() + "\n… (truncated)"
    return text


def main(argv: list[str]) -> int:
    from state import repo_root

    root = repo_root()
    action = argv[0] if argv else "show"

    if action == "show":
        print(show(root, argv[1]) if len(argv) > 1 else render(root))
        return 0

    if action == "add":
        title = " ".join(argv[1:]).strip() or "untitled"
        text = sys.stdin.read().strip()
        if not text:
            print("lessons: nothing to record (body was empty)")
            return 0
        lesson = append(title, text, root)
        print(f"lessons: recorded {lesson.id} — {title}")
        return 0

    if action == "revise":
        if len(argv) < 2:
            print("lessons: usage: lessons.py revise <id> <title...>", file=sys.stderr)
            return 2
        lesson_id, title = argv[1], " ".join(argv[2:]).strip() or "untitled"
        text = sys.stdin.read().strip()
        if not text:
            print("lessons: nothing to record (body was empty)")
            return 0
        try:
            lesson = revise(lesson_id, title, text, root)
        except ValueError as e:
            print(f"lessons: {e}", file=sys.stderr)
            return 1
        print(f"lessons: {lesson_id} superseded by {lesson.id} — {title}")
        return 0

    print(f"lessons: unknown action {action!r} (show | show <id> | add | revise)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
