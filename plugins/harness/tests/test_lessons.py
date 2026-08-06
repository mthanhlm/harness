"""What the project has learned, and the mechanism for saying it was wrong.

The roadmap this replaces recorded decisions and deferred work, and the
deferred bullets were the ones that misled — every miss traced back to a
`deferred:` describing a defect that had since been fixed. This file keeps
only durable lessons, and the property that makes the update mechanism worth
having: `revise` must never lose the old text, only mark it superseded.
"""

from __future__ import annotations

import lessons


def test_a_lesson_survives_into_the_next_session(tmp_path):
    lessons.append("Releasing takes two commands", "`update` refreshes the checkout only.", tmp_path)

    assert "Releasing takes two commands" in lessons.read(tmp_path)
    assert "`update` refreshes the checkout only." in lessons.read(tmp_path)


def test_the_newest_lesson_is_first(tmp_path):
    lessons.append("older", "a", tmp_path)
    lessons.append("newer", "b", tmp_path)

    text = lessons.read(tmp_path)
    assert text.index("newer") < text.index("older")


def test_reading_a_project_with_no_lessons_is_not_an_error(tmp_path):
    assert lessons.read(tmp_path) == ""
    assert lessons.entries(tmp_path) == []


def test_ids_allocate_monotonically(tmp_path):
    a = lessons.append("first", "x", tmp_path)
    b = lessons.append("second", "y", tmp_path)

    assert a.id == "L1"
    assert b.id == "L2"


def test_revise_keeps_the_old_body_intact_and_marks_it_superseded(tmp_path):
    """The assertion that matters most in this file. A mechanism that silently
    overwrote the old lesson would be indistinguishable from one that had never
    been wrong, which is precisely the failure this replaces."""
    old = lessons.append(
        "One command is enough to release",
        "Body of the lesson that turned out to be wrong.",
        tmp_path,
    )

    new = lessons.revise(
        old.id,
        "Releasing this plugin takes two commands",
        "`marketplace update` refreshes the checkout and installs nothing.",
        tmp_path,
    )

    found = {e.id: e for e in lessons.entries(tmp_path)}
    assert found[old.id].body == "Body of the lesson that turned out to be wrong."
    assert found[old.id].title == "One command is enough to release"
    assert found[old.id].superseded_by == new.id
    assert new.supersedes == old.id
    assert new.title == "Releasing this plugin takes two commands"

    # And on disk, not only through the parser.
    text = lessons.read(tmp_path)
    assert "Body of the lesson that turned out to be wrong." in text
    assert f"superseded-by: {new.id}" in text
    assert f"supersedes: {old.id}" in text


def test_revising_an_unknown_id_raises_rather_than_silently_writing(tmp_path):
    lessons.append("a lesson", "body", tmp_path)

    try:
        lessons.revise("L99", "new title", "new body", tmp_path)
        raised = False
    except ValueError:
        raised = True

    assert raised
    assert "L99" not in lessons.read(tmp_path)


def test_a_revised_id_is_never_reused(tmp_path):
    old = lessons.append("first", "x", tmp_path)
    new = lessons.revise(old.id, "second", "y", tmp_path)
    newer = lessons.append("third", "z", tmp_path)

    ids = [e.id for e in lessons.entries(tmp_path)]
    assert len(ids) == len(set(ids)), f"an id was reused: {ids}"
    assert newer.id not in (old.id, new.id)


def test_a_superseded_lesson_is_marked_in_the_index_and_the_live_one_is_not(tmp_path):
    old = lessons.append("baggage limit is 20kg", "decided at the time", tmp_path)
    new = lessons.revise(old.id, "baggage limit is 23kg", "the actual rule", tmp_path)

    index = lessons.render(tmp_path)
    assert "baggage limit is 23kg" in index
    assert "baggage limit is 20kg" in index, "superseded lessons stay in the index, marked"
    assert f"superseded by {new.id}" in index


def test_a_malformed_file_degrades_to_no_entries_rather_than_raising(tmp_path):
    path = tmp_path / ".harness"
    path.mkdir()
    (path / "lessons.md").write_bytes(b"\xff\xfe not a heading at all\x00")

    assert lessons.entries(tmp_path) == []
    assert lessons.render(tmp_path) == "lessons: nothing recorded for this project yet"


def test_a_heading_with_no_id_or_date_is_skipped_not_fatal(tmp_path):
    """A hand-written heading that does not match the contract loses tracking
    of that one entry rather than the whole file."""
    path = tmp_path / ".harness"
    path.mkdir()
    (path / "lessons.md").write_text(
        "# Lessons\n\n## Some hand-written note\n\nBody.\n\n"
        "## L1 · 2026-08-01 · a real lesson\n\nBody two.\n",
        encoding="utf-8",
    )

    found = lessons.entries(tmp_path)
    assert [e.title for e in found] == ["a real lesson"]


def test_show_returns_one_lesson_in_full(tmp_path):
    entry = lessons.append("a lesson", "the body of it", tmp_path)

    assert "the body of it" in lessons.show(tmp_path, entry.id)
    assert "no entry" in lessons.show(tmp_path, "L99")


def test_render_truncates_to_the_given_limit(tmp_path):
    """The guard `session_start` depends on: a repo-supplied file must not be
    able to flood a session's opening context."""
    lessons.append("a" * 5000, "b" * 5000, tmp_path)

    text = lessons.render(tmp_path, limit=500)

    assert len(text) <= 520, f"render did not honour its limit: {len(text)}"


def test_render_without_a_limit_is_not_truncated(tmp_path):
    lessons.append("short title", "short body", tmp_path)

    assert "truncated" not in lessons.render(tmp_path)


def test_main_add_then_show_round_trips(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lessons, "repo_root", lambda: tmp_path, raising=False)
    import state

    monkeypatch.setattr(state, "repo_root", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("the body\n"))

    lessons.main(["add", "a", "title"])
    out = capsys.readouterr().out
    assert "recorded L1" in out


def test_main_revise_reports_the_supersession(tmp_path, monkeypatch, capsys):
    import state

    monkeypatch.setattr(state, "repo_root", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("first body\n"))
    lessons.main(["add", "first", "title"])

    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("corrected body\n"))
    lessons.main(["revise", "L1", "corrected", "title"])

    out = capsys.readouterr().out
    assert "L1 superseded by L2" in out


def test_a_hand_written_note_survives_an_automatic_append(tmp_path):
    """The file's own preamble invites hand editing, so someone will edit it.

    `entries` merely skips a chunk it cannot parse, which reads as harmless — but
    the write path rebuilt the whole file out of `entries`, so the next automatic
    append deleted the note. Losing what a person wrote, silently, on a write
    they did not make, is worse than any lesson this module fails to record.
    """
    lessons.append("recorded normally", "body", tmp_path)
    path = lessons.lessons_path(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n## Do not run the migration on a Friday\n\nWe learned this in March.\n",
        encoding="utf-8",
    )

    lessons.append("a later session", "another body", tmp_path)

    after = path.read_text(encoding="utf-8")
    assert "Do not run the migration on a Friday" in after
    assert "We learned this in March." in after


def test_a_heading_inside_a_body_does_not_split_the_lesson(tmp_path):
    """`_split` cuts the file on `^## `, so a body carrying one would be read as
    the start of the next lesson — the tail would fail to parse, and the write
    after that would drop it. The roadmap demoted these for the same reason."""
    lessons.append(
        "Retry queue drops big messages",
        "Body line one.\n\n## Why\n\nBecause the broker caps at 256KB.",
        tmp_path,
    )
    lessons.append("something later", "unrelated", tmp_path)

    recorded = [e for e in lessons.entries(tmp_path) if e.title.startswith("Retry queue")]
    assert len(recorded) == 1
    assert "Because the broker caps at 256KB." in recorded[0].body


def test_concurrent_appends_do_not_destroy_a_lesson_already_on_file(tmp_path):
    """Every session in a repo writes this one file, and two reaching SessionEnd
    together both read it, both rebuild it, and the later write wins with a list
    built before the earlier one existed.

    Measured before the lock: eight concurrent appends against a file holding one
    lesson produced three to seven entries across four runs, and in one of them
    the pre-existing lesson was gone — a durable record destroyed, not just a new
    one dropped.
    """
    import subprocess
    import sys
    from pathlib import Path

    scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    lessons.append("seed", "must survive every concurrent write", tmp_path)

    program = (
        "import sys; sys.path.insert(0, %r); import lessons;"
        " lessons.append('worker ' + sys.argv[1], 'body ' + sys.argv[1], %r)"
        % (scripts, str(tmp_path))
    )
    workers = [
        subprocess.Popen([sys.executable, "-c", program, str(i)]) for i in range(6)
    ]
    for worker in workers:
        worker.wait(timeout=60)

    titles = [e.title for e in lessons.entries(tmp_path)]
    assert "seed" in titles, "an already-recorded lesson was destroyed by a concurrent write"
    assert len(titles) == 7, f"lost updates: {sorted(titles)}"
