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


def test_old_entries_fall_off(tmp_path):
    """An append-only log rots into a wall nobody reads, which is the same as
    having no roadmap at all."""
    for i in range(roadmap.MAX_ENTRIES + 8):
        roadmap.append(tmp_path, f"entry-{i}", f"- decided: {i}")

    text = roadmap.read(tmp_path)
    assert text.count("\n## ") == roadmap.MAX_ENTRIES
    assert "entry-0" not in text
    assert f"entry-{roadmap.MAX_ENTRIES + 7}" in text


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
