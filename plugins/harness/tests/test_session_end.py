"""Deriving the roadmap entry from the contract.

This is the only part of the plugin that writes the project's memory, and it
runs inside a `SessionEnd` hook wrapped in `except Exception: pass` — so a total
failure is indistinguishable from "no approved contract", with nobody watching.
It shipped with no test and a live truncation bug, which is how it got one.

The fixtures are hard-wrapped at about eighty columns on purpose. A contract is
written that way, and a synthetic one-line-per-bullet fixture agrees with the
bug instead of catching it.
"""

from __future__ import annotations

import session_end
from contract import Contract

WRAPPED = """# Plan: Turn the gates back on

status: approved
verdict: patch

## Scope

Files this will change:
- src/one.py — first

Explicitly NOT changing:
- `state.py` writer sharding and `subagent_stop.py` — reviewers and refuters
  fired 25 times today and still use them. Removing that machinery is a
  separate call.
- `detect.py` — a root `pytest.ini` is sufficient; I verified this by running
  `build_profile` rather than reasoning about it.

## Verdict

Patch. The architect read the code and ran it against real data: 138 passing
tests, every module states its contract, and each fix lands on an existing
seam. Nothing here justifies a rewrite.

## Disagreement

None.

## Risks

Something.
"""


def test_a_deferred_item_is_recorded_as_a_whole_sentence():
    """The bug this file was written for.

    Reading one physical line per bullet cut ten of twenty-seven real entries
    mid-clause. `- deferred: detect.py —` tells the next session nothing, which
    is the only thing the roadmap exists to prevent — so a truncated entry is
    not a cosmetic flaw, it is the feature failing while appearing to work.
    """
    deferred = session_end._not_changing(WRAPPED)

    assert len(deferred) == 2
    assert deferred[0].endswith("separate call.")
    assert deferred[1].endswith("rather than reasoning about it.")
    assert "  " not in deferred[0], "continuation lines must be joined on single spaces"


def test_the_verdict_is_recorded_without_ending_mid_clause():
    """`_summary` joins the wrapped paragraph; the first *line* is a fragment."""
    title, body = session_end.entry_for(Contract(WRAPPED))

    assert title == "Turn the gates back on"
    verdict_line = next(line for line in body.splitlines() if line.startswith("- decided:"))
    assert verdict_line.rstrip().endswith((".", "…"))
    assert "138 passing tests" in verdict_line


def test_no_disagreement_is_not_recorded_as_a_decision():
    """"None." is the honest answer to Disagreement and must not become an entry."""
    body = session_end.entry_for(Contract(WRAPPED))[1]

    assert "- decided: None" not in body
    assert len([ln for ln in body.splitlines() if ln.startswith("- decided:")]) == 1


def test_an_unapproved_contract_writes_nothing(tmp_path, monkeypatch):
    """The roadmap records decisions, and a pending plan has not made any."""
    pending = WRAPPED.replace("status: approved", "status: pending")
    monkeypatch.setattr(session_end.contract_mod, "load", lambda _: Contract(pending))
    written = []
    monkeypatch.setattr(session_end.roadmap, "append", lambda *a: written.append(a))

    session_end.write_roadmap({"session_id": "s", "repo_root": str(tmp_path)})

    assert written == []


def test_the_same_session_does_not_stack_duplicate_entries(tmp_path, monkeypatch):
    """`SessionEnd` can fire more than once; the roadmap is capped and ordered."""
    monkeypatch.setattr(session_end.contract_mod, "load", lambda _: Contract(WRAPPED))
    session = {"session_id": "s", "repo_root": str(tmp_path)}

    session_end.write_roadmap(session)
    first = session_end.roadmap.read(tmp_path)
    session_end.write_roadmap(session)

    assert session_end.roadmap.read(tmp_path) == first
    assert first.count("Turn the gates back on") == 1


def test_a_contract_with_nothing_to_say_produces_no_entry():
    """Padding is worse than an empty roadmap — the file's own docstring says so."""
    bare = "# Plan: x\n\nstatus: approved\nverdict: patch\n\n## Goal\n\nSomething.\n"

    assert session_end.entry_for(Contract(bare)) is None
