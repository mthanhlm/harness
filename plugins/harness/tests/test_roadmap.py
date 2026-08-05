"""The project's memory, and keeping the tool's artifacts out of the product."""

from __future__ import annotations

import subprocess
from pathlib import Path

import local_ignore
import roadmap


def test_a_decision_survives_into_the_next_session(tmp_path):
    """The whole reason this exists: work is done, a problem is found, and the
    next session has never heard of it."""
    roadmap.append(tmp_path, "Trust boundary", "- deferred: fix the ledger double-count")

    assert "deferred: fix the ledger double-count" in roadmap.read(tmp_path)
    assert "Trust boundary" in roadmap.read(tmp_path)


def test_the_newest_entry_is_first(tmp_path):
    """Newest-first, because the recent decisions are the ones still in force."""
    roadmap.append(tmp_path, "older", "- decided: a")
    roadmap.append(tmp_path, "newer", "- decided: b")

    text = roadmap.read(tmp_path)
    assert text.index("newer") < text.index("older")


def test_old_entries_leave_the_index_without_leaving_the_file(tmp_path):
    """Changed in 0.8, deliberately. The cap used to delete, because the whole
    file was read on every plan and 36KB was nine thousand tokens. Now only the
    index is read, so the cap retires an entry instead — it stops being offered
    as current and stays available as evidence."""
    for i in range(roadmap.MAX_ENTRIES + 8):
        roadmap.append(tmp_path, f"entry-{i}", f"- decided: {i}")

    live = [e for e in roadmap.entries(tmp_path) if e.active]
    assert len(live) == roadmap.MAX_ENTRIES
    assert "entry-0" not in roadmap.index(tmp_path)
    assert "entry-0" in roadmap.read(tmp_path)
    assert f"entry-{roadmap.MAX_ENTRIES + 7}" in roadmap.index(tmp_path)


def test_a_superseded_decision_stops_being_offered_as_current(tmp_path):
    """The failure this exists to prevent: a rule that was retired months ago is
    still cited as if it held, because the log only ever appended."""
    old = roadmap.append(tmp_path, "baggage limit is 20kg", "- decided: 20kg")
    new = roadmap.append(tmp_path, "baggage limit is 23kg", "- decided: 23kg", supersedes=old.id)

    index = roadmap.index(tmp_path)
    assert "23kg" in index
    assert "20kg" not in index, "a reversed decision is still being presented as in force"

    # Still evidence, though — retired is not deleted.
    assert "20kg" in roadmap.read(tmp_path)
    assert f"SUPERSEDED by {new.id}" in roadmap.show(tmp_path, old.id)


def test_the_index_is_a_fraction_of_the_file(tmp_path):
    """The whole point of layering the read. Bodies are what cost tokens; the
    index carries only what decides whether to open one."""
    for i in range(20):
        roadmap.append(tmp_path, f"decision {i}", "- decided: " + ("x" * 400))

    assert len(roadmap.index(tmp_path)) < len(roadmap.read(tmp_path)) / 10


def test_an_outcome_is_recorded_without_rewriting_the_body(tmp_path):
    """Deterministic edit. Asking a model to rewrite the file to add one field
    erases the rare detail that was the reason for the entry."""
    entry = roadmap.append(tmp_path, "a design", "- decided: keep this exact wording")

    assert roadmap.set_outcome(tmp_path, entry.id, "reworked")

    again = next(e for e in roadmap.entries(tmp_path) if e.id == entry.id)
    assert again.outcome == "reworked"
    assert again.body == "- decided: keep this exact wording"
    assert "reworked" in roadmap.index(tmp_path)


def test_an_unknown_outcome_is_refused(tmp_path):
    entry = roadmap.append(tmp_path, "a design", "- decided: x")

    assert roadmap.set_outcome(tmp_path, entry.id, "probably-fine") is False
    assert next(e for e in roadmap.entries(tmp_path) if e.id == entry.id).outcome == "open"


def test_entries_written_before_08_still_parse_and_get_ids(tmp_path):
    """There are eighteen of these on disk. Losing them to a format change would
    cost exactly what this file exists to preserve."""
    legacy = (
        "# Roadmap\n\n"
        "## 2026-07-29 — Turn the gates back on\n\n- decided: something real\n\n"
        "## 2026-07-28 — An older call\n\n- deferred: something else\n"
    )
    (tmp_path / ".harness").mkdir()
    roadmap.roadmap_path(tmp_path).write_text(legacy, encoding="utf-8")

    found = roadmap.entries(tmp_path)
    assert [e.title for e in found] == ["Turn the gates back on", "An older call"]
    assert [e.date for e in found] == ["2026-07-29", "2026-07-28"]
    # Numbered oldest-first, so the ids read in the order the decisions were taken.
    assert [e.id for e in found] == ["r2", "r1"]
    assert all(e.active and e.outcome == "open" for e in found)

    roadmap.append(tmp_path, "a new one", "- decided: newest")
    after = roadmap.entries(tmp_path)
    assert [e.id for e in after] == ["r3", "r2", "r1"], "ids must not be reissued"
    assert "something real" in roadmap.show(tmp_path, "r2")


def test_an_entry_pasted_in_by_hand_does_not_collide_with_an_existing_id(tmp_path):
    """The mixed case, which is the only one that can actually reissue an id.
    Once every entry is numbered the backfill is inert, so a test that appends to
    a wholly legacy file proves nothing — this file invites hand editing, and a
    pasted entry arrives with no metadata beside entries that have it."""
    roadmap.append(tmp_path, "first", "- decided: a")
    roadmap.append(tmp_path, "second", "- decided: b")
    path = roadmap.roadmap_path(tmp_path)
    path.write_text(
        path.read_text().replace("# Roadmap\n", "# Roadmap\n\n## 2026-08-05 — pasted by hand\n\n- decided: c\n", 1),
        encoding="utf-8",
    )

    ids = [e.id for e in roadmap.entries(tmp_path)]

    assert len(ids) == len(set(ids)), f"an id was reissued: {ids}"
    assert {"r1", "r2"} <= set(ids), "an existing entry lost or changed its id"


def test_deleting_the_metadata_line_by_hand_keeps_the_entry(tmp_path):
    """The file invites editing. Losing tracking is survivable; losing the note
    is not, so the metadata must fail in that direction."""
    roadmap.append(tmp_path, "hand edited", "- decided: keep me")
    path = roadmap.roadmap_path(tmp_path)
    path.write_text(
        "\n".join(l for l in path.read_text().splitlines() if "harness:" not in l), encoding="utf-8"
    )

    found = roadmap.entries(tmp_path)
    assert len(found) == 1
    assert "keep me" in found[0].body
    assert found[0].active


def test_entries_touching_a_path_are_findable(tmp_path):
    """What lets the challenger say "you decided this in r12" instead of
    "have you considered"."""
    roadmap.append(tmp_path, "the fence", "- decided: scripts/stop_gate.py grew a latch")
    roadmap.append(tmp_path, "unrelated", "- decided: nothing to do with it")

    hits = roadmap.touching(tmp_path, ["scripts/stop_gate.py"])
    assert [e.title for e in hits] == ["the fence"]
    assert roadmap.touching(tmp_path, []) == []


def test_reading_a_project_with_no_roadmap_is_not_an_error(tmp_path):
    assert roadmap.read(tmp_path) == ""


def test_an_entry_is_not_mangled_by_the_next_one(tmp_path):
    """Entries are split on headings, so a body containing one must survive."""
    roadmap.append(tmp_path, "first", "- decided: keep it\n- deferred: something else")
    roadmap.append(tmp_path, "second", "- decided: another")

    text = roadmap.read(tmp_path)
    assert "- decided: keep it" in text
    assert "- deferred: something else" in text


def test_the_tools_artifacts_are_excluded_locally(git_repo):
    """`.gitignore` is tracked, so writing there would put a change in the
    user's next commit that they did not make. This must not."""
    added = local_ignore.ensure(git_repo)

    assert set(added) == set(local_ignore.ENTRIES)
    exclude = (git_repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".codegraph/" in exclude and ".harness/" in exclude

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(git_repo), capture_output=True, text=True
    ).stdout
    assert ".gitignore" not in status, "the repo's tracked ignore file was modified"


def test_an_excluded_roadmap_does_not_show_up_as_a_change(git_repo):
    local_ignore.ensure(git_repo)
    roadmap.append(git_repo, "a decision", "- decided: something")

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(git_repo), capture_output=True, text=True
    ).stdout
    assert ".harness" not in status


def test_excluding_twice_adds_nothing(git_repo):
    local_ignore.ensure(git_repo)
    before = (git_repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")

    assert local_ignore.ensure(git_repo) == []
    assert (git_repo / ".git" / "info" / "exclude").read_text(encoding="utf-8") == before


def test_outside_a_repo_it_declines_quietly(tmp_path):
    assert local_ignore.ensure(tmp_path) == []


def test_a_heading_inside_a_body_does_not_split_the_entry(tmp_path):
    """Entries are delimited by `## `, so a body containing one would fragment
    into several — burning slots against the cap and scattering the user's text."""
    roadmap.append(tmp_path, "one entry", "- decided: x\n## Why\nBecause y.")

    assert len(roadmap._entries(roadmap.read(tmp_path))) == 1
    assert "Because y." in roadmap.read(tmp_path)


def test_a_hand_edited_file_keeps_its_newest_entry(tmp_path):
    """The file invites editing. Deleting the preamble used to silently eat the
    entry that followed it."""
    roadmap.append(tmp_path, "keep me", "- decided: important")
    path = roadmap.roadmap_path(tmp_path)
    path.write_text(path.read_text().split("\n## ", 1)[1].join(["## ", ""]), encoding="utf-8")

    roadmap.append(tmp_path, "newer", "- decided: another")

    assert "keep me" in roadmap.read(tmp_path)


def test_a_hand_written_entry_keeps_its_title_across_appends(tmp_path):
    """The file's own header says "edit or delete anything here freely", and a
    hand-written entry has no date on its heading.

    `_render` wrote `## {date} — {title}` unconditionally, so that entry came back
    as `##  — Title`; re-parsing found no date and swallowed the em-dash into the
    title, and the next append rendered `##  — — Title`. It compounded on every
    single append, in the one line the index shows and the challenger cites — so
    the memory this module exists to provide degraded a little every session.
    """
    path = tmp_path / ".harness"
    path.mkdir()
    (path / "roadmap.md").write_text(
        "# Roadmap\n\n## Switched to server components\n\nDecided in review.\n",
        encoding="utf-8",
    )

    for i in range(3):
        roadmap.append(tmp_path, f"entry {i}", "body")

    titles = [e.title for e in roadmap.entries(tmp_path)]
    assert "Switched to server components" in titles, titles
    # Asserted on the file, not only through the parser. Stripping the separators
    # on read heals the damage but also hides it: `entries()` comes back clean
    # while every rewrite still puts `##  — Title` back on disk, which is what a
    # person opening the roadmap actually sees. Reverting the render fix passed
    # this test until this line existed.
    text = roadmap.read(tmp_path)
    assert "## Switched to server components" in text, text


def test_a_title_the_old_code_already_mangled_is_repaired(tmp_path):
    """Rendering correctly stops the damage but does not undo it, and the entries
    already on disk have been through this. Reading strips the leading separators
    back off, so the next write puts the real title back."""
    path = tmp_path / ".harness"
    path.mkdir()
    (path / "roadmap.md").write_text(
        "# Roadmap\n\n## — — Reworked the session state layer\n\nBody.\n",
        encoding="utf-8",
    )

    assert [e.title for e in roadmap.entries(tmp_path)] == ["Reworked the session state layer"]


def test_a_dated_entry_still_renders_its_date(tmp_path):
    """The direction the fix must not overshoot into: dropping the separator
    whenever it is inconvenient would take the date out of every index line, and
    the date is what makes churn evidence readable."""
    entry = roadmap.append(tmp_path, "Dated decision", "body")

    text = roadmap.read(tmp_path)
    assert f"## {entry.date} — Dated decision" in text, text
    assert entry.date in roadmap.index(tmp_path)


def test_whether_an_entry_is_excluded_is_answered_by_reading_the_file(git_repo):
    """`ensure` returns what it *added*, so an empty list means either "already
    there" or "could not write" — indistinguishable.

    `codegraph_ready` used that to decide what to tell the user, and told them to
    add `.codegraph/` to `.gitignore`: a *tracked* file, which is the exact
    pollution `local_ignore` exists to avoid and whose docstring says so. The
    plugin was instructing the user to undo its own design decision.
    """
    assert local_ignore.excluded(git_repo, ".codegraph/") is False

    local_ignore.ensure(git_repo)

    assert local_ignore.excluded(git_repo, ".codegraph/") is True
    # And still true on a second call, when `ensure` adds nothing.
    assert local_ignore.ensure(git_repo) == []
    assert local_ignore.excluded(git_repo, ".codegraph/") is True


def test_nothing_is_reported_as_excluded_outside_a_repository(tmp_path):
    """The direction that matters: claiming an exclusion that does not exist
    would leave the artifacts to show up in the user's next commit."""
    assert local_ignore.excluded(tmp_path, ".codegraph/") is False
