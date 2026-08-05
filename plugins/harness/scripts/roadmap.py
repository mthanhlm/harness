#!/usr/bin/env python3
"""What this project decided, what became of it, and what is still in force.

Every session starts from nothing. Work gets done, a real problem gets found and
reported, and the next session has never heard of it — so the same ground is
re-covered and the same deferred item gets deferred again. That is the difference
between a tool and a colleague, and it is a file, not a feature.

Three deliberate limits, because an append-only log rots into a wall nobody reads:

- **Decisions and deferred work only.** Not a session narrative. If it does not
  change what someone would do next, it does not go in.
- **A hard cap, on entries and on bytes.** Old entries fall off. Counting entries
  alone was not enough: twenty-five entries reached 36KB in eight days, and a
  roadmap nobody can read to the end is the same as no roadmap.
- **Superseded entries leave the index.** A decision that has been reversed is
  still *evidence* — it says this ground was covered and which way it went — but
  presenting it beside the decision that reversed it is how a codebase acquires
  two of everything. It stays in the file, named by its successor, and out of
  what gets read at the start of a plan.

The last one is the difference between remembering and learning. An agent that
only appends keeps citing a rule that was retired months ago; one that supersedes
replaces it and moves on.

Reading is layered for the same reason the entries are capped. `show` prints an
index — one line per entry still in force — and `show <id>` prints one body. The
whole file was previously read at the start of every plan; at 36KB that is roughly
nine thousand tokens spent to surface the two entries that mattered.

It lives in the repository so it survives a plugin reinstall and moves with the
code — and is excluded locally, because it belongs to the tooling rather than to
the product being shipped.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# The cap that matters is on entries *still in force*, because that is what the
# index prints and the index is the only part read at the start of a plan. The
# byte cap is a runaway guard, not a context budget: before 0.8 the whole file
# was read on every plan, so 36KB was 9,000 tokens and counting entries alone was
# not enough. Now the file is read one entry at a time and its size costs nothing.
#
# Set the byte cap where it trimmed the real 36KB file on the first append and it
# deletes five entries — including the one just superseded, destroying the audit
# trail at the exact moment it was created. Superseded entries are cheap to keep
# and are the evidence the challenger cites, so they are kept.
MAX_ENTRIES = 25
MAX_BYTES = 250_000

OUTCOMES = ("open", "held", "reworked", "abandoned")

PREAMBLE = """# Roadmap

Decisions taken and work deliberately deferred, newest first. Written by
`/harness:plan`; edit or delete anything here freely — it is notes, not state.

Each entry carries a machine-readable line. Delete it and the entry still reads
fine — it just stops being tracked, which is the safe direction to fail in.
"""

# Metadata rides in an HTML comment rather than the heading: invisible when the
# markdown renders, obvious in an editor, and survivable. The file invites hand
# editing, so a user who deletes the comment must lose tracking, not the entry —
# `_parse_meta` returning nothing degrades to exactly the pre-0.8 format, which
# is what the 18 entries already on disk are.
_META_RE = re.compile(r"<!--\s*harness:\s*(.*?)\s*-->")
_META_PAIR_RE = re.compile(r"(\w+)=(\S+)")
_HEAD_RE = re.compile(r"^##\s+(?:(\d{4}-\d{2}-\d{2})\s*[—–-]\s*)?(.*)$")
_ID_RE = re.compile(r"^r(\d+)$")
# Heals a title this module previously mangled, and stops it compounding.
#
# `_render` used to write `## {date} — {title}` unconditionally, so an entry with
# no date came out as `##  — Title`. Re-parsing that finds no date, so the em-dash
# falls into the title group — and the next append renders `##  — — Title`. The
# file invites hand editing and a hand-written `## Some decision` has no date, so
# every append added another dash to it, forever, in the one line the index shows.
_LEADING_DASH_RE = re.compile(r"^(?:[—–-]\s+)+")


@dataclass
class Entry:
    """One decision, and what became of it."""

    title: str
    body: str
    date: str = ""
    id: str = ""
    status: str = "active"
    superseded_by: str = ""
    outcome: str = "open"
    raw: str = field(default="", repr=False)

    @property
    def active(self) -> bool:
        return self.status != "superseded"

    def index_line(self) -> str:
        mark = self.outcome if self.outcome != "open" else ""
        ident = self.id or "--"
        return f"{ident:>4}  {self.date or '?':10}  {mark:9} {self.title}".rstrip()


def roadmap_path(root: Path) -> Path:
    return root / ".harness" / "roadmap.md"


def read(root: Path) -> str:
    try:
        return roadmap_path(root).read_text(encoding="utf-8")
    except OSError:
        return ""


def _split(text: str) -> list[str]:
    """Raw `## ` sections, newest first.

    Anchored to the start of a line *and* tolerant of the file beginning with a
    heading, because the header invites editing it by hand — and the first
    version silently ate the newest entry of anyone who deleted the preamble.
    """
    marks = [m.start() for m in re.finditer(r"^## ", text, re.MULTILINE)]
    return [text[a:b].rstrip() for a, b in zip(marks, marks[1:] + [len(text)])]


def _parse_meta(chunk: str) -> dict[str, str]:
    match = _META_RE.search(chunk)
    if not match:
        return {}
    return dict(_META_PAIR_RE.findall(match.group(1)))


def _parse(chunk: str) -> Entry:
    lines = chunk.splitlines()
    head = _HEAD_RE.match(lines[0]) if lines else None
    when, title = (head.group(1) or "", head.group(2).strip()) if head else ("", "untitled")
    title = _LEADING_DASH_RE.sub("", title).strip() or title

    meta = _parse_meta(chunk)
    body = "\n".join(line for line in lines[1:] if not _META_RE.search(line)).strip()

    outcome = meta.get("outcome", "open")
    return Entry(
        title=title,
        body=body,
        date=meta.get("date", when),
        id=meta.get("id", ""),
        status=meta.get("status", "active"),
        superseded_by=meta.get("superseded_by", ""),
        outcome=outcome if outcome in OUTCOMES else "open",
        raw=chunk,
    )


# Kept under its old name: the tests and `session_end` both reach for it, and
# renaming a working seam is how a refactor acquires a bug it did not need.
def _entries(text: str) -> list[str]:
    return _split(text)


def entries(root: Path) -> list[Entry]:
    found = [_parse(chunk) for chunk in _split(read(root))]
    _backfill_ids(found)
    return found


def _next_id(existing: list[Entry]) -> str:
    used = [int(m.group(1)) for e in existing if (m := _ID_RE.match(e.id))]
    return f"r{max(used, default=0) + 1}"


def _backfill_ids(existing: list[Entry]) -> None:
    """Give the entries written before 0.8 an id, once, in place.

    Without this the eighteen entries already on disk index as `--` and cannot be
    fetched by `show <id>` — the index would list them and then have no way to
    open them. Numbering runs oldest-first so the ids read in the order the
    decisions were taken, and an id once assigned is never reissued.
    """
    counter = max((int(m.group(1)) for e in existing if (m := _ID_RE.match(e.id))), default=0)
    for entry in reversed(existing):
        if not entry.id:
            counter += 1
            entry.id = f"r{counter}"


def _render(entry: Entry) -> str:
    meta = [f"id={entry.id}", f"date={entry.date}", f"status={entry.status}"]
    if entry.superseded_by:
        meta.append(f"superseded_by={entry.superseded_by}")
    meta.append(f"outcome={entry.outcome}")
    head = f"## {entry.date} — {entry.title}" if entry.date else f"## {entry.title}"
    return f"{head}\n\n<!-- harness: {' '.join(meta)} -->\n\n{entry.body.strip()}\n"


def _prune(kept: list[Entry]) -> list[Entry]:
    """Trim to the caps, dropping what is least likely to change a decision.

    The count cap applies to *active* entries only — they are what the index
    prints, and a superseded entry costs nothing to keep because nothing reads it
    unless it is asked for by id. Dropping the oldest active entry retires it
    rather than deleting it, so the trail of what was decided survives the cap
    that the entry itself did not.

    The byte cap is the runaway guard underneath, and it does delete. Superseded
    entries go first there, oldest first, because whatever they said has already
    been answered by the entry that replaced it.
    """
    live = [e for e in kept if e.active]
    for stale in live[MAX_ENTRIES:]:
        stale.status = "superseded"

    while len(kept) > 1 and sum(len(_render(e)) for e in kept) > MAX_BYTES:
        retired = [i for i, e in enumerate(kept) if not e.active]
        kept.pop(retired[-1] if retired else len(kept) - 1)
    return kept


def _write(root: Path, kept: list[Entry]) -> Path:
    path = roadmap_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(_render(e) for e in kept)
    path.write_text(PREAMBLE + "\n" + body + "\n", encoding="utf-8")
    return path


def append(
    root: Path,
    title: str,
    body: str,
    *,
    supersedes: str | list[str] | None = None,
    outcome: str = "open",
) -> Entry:
    """Add one dated entry at the top, retire what it replaces, and trim."""
    existing = entries(root)

    # A heading inside a body would split one entry into several. Demote rather
    # than reject: the body is the user's text, and losing part of it is worse
    # than changing its level.
    safe = re.sub(r"^## ", "### ", body.strip(), flags=re.MULTILINE)

    entry = Entry(
        title=title.strip() or "untitled",
        body=safe,
        date=date.today().isoformat(),
        id=_next_id(existing),
        outcome=outcome if outcome in OUTCOMES else "open",
    )

    retire = [supersedes] if isinstance(supersedes, str) else list(supersedes or [])
    for old in existing:
        if old.id and old.id in retire:
            old.status = "superseded"
            old.superseded_by = entry.id

    _write(root, _prune([entry] + existing))
    return entry


def set_outcome(root: Path, entry_id: str, outcome: str) -> bool:
    """Record what became of one entry. Deterministic: only the meta line moves.

    Nothing here asks a model to rewrite the file. Successive attempts at brevity
    erase the rare detail that was the whole reason for the entry, and an entry
    that has been quietly generalised is worse than one that is missing, because
    it still reads as authoritative.
    """
    if outcome not in OUTCOMES:
        return False
    found = entries(root)
    for entry in found:
        if entry.id == entry_id:
            entry.outcome = outcome
            _write(root, found)
            return True
    return False


def index(root: Path) -> str:
    """One line per entry still in force."""
    live = [e for e in entries(root) if e.active]
    if not live:
        return "roadmap: nothing recorded for this project yet"
    lines = [e.index_line() for e in live]
    retired = len(entries(root)) - len(live)
    tail = f"\n({retired} superseded, hidden)" if retired else ""
    return (
        "Decisions still in force. `roadmap.py show <id>` for one in full.\n"
        "  id  date        outcome   what was decided\n" + "\n".join(lines) + tail
    )


def show(root: Path, entry_id: str) -> str:
    for entry in entries(root):
        if entry.id == entry_id:
            note = ""
            if not entry.active:
                note = f"\nSUPERSEDED by {entry.superseded_by or 'a later entry'}."
            return f"## {entry.date} — {entry.title}\noutcome: {entry.outcome}{note}\n\n{entry.body}"
    return f"roadmap: no entry {entry_id!r}"


def touching(root: Path, paths: list[str]) -> list[Entry]:
    """Entries that mention any of these paths — evidence, for triage."""
    wanted = [p for p in paths if p]
    if not wanted:
        return []
    return [e for e in entries(root) if any(p in e.raw for p in wanted)]


def main() -> int:
    from state import repo_root

    root = repo_root()
    argv = sys.argv[1:]
    action = argv[0] if argv else "show"

    if action == "show":
        print(show(root, argv[1]) if len(argv) > 1 else index(root))
        return 0

    if action == "outcome" and len(argv) > 2:
        ok = set_outcome(root, argv[1], argv[2])
        print(f"roadmap: {argv[1]} → {argv[2]}" if ok else f"roadmap: could not set {argv[1]}")
        return 0

    if action == "touching":
        found = touching(root, argv[1:])
        print("\n".join(e.index_line() for e in found) or "roadmap: no entry covers those paths")
        return 0

    if action == "append":
        title = " ".join(argv[1:]).strip() or "untitled"
        body = sys.stdin.read().strip()
        if not body:
            print("roadmap: nothing to record (body was empty)")
            return 0
        entry = append(root, title, body)
        print(f"roadmap: recorded {entry.id} — {title}")
        return 0

    if action == "json":
        print(json.dumps([e.__dict__ for e in entries(root)], default=str))
        return 0

    print(f"roadmap: unknown action {action!r} (show | show <id> | outcome | touching | append)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
