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


BUDGETED = """# Plan: A modest change

status: approved
verdict: patch
Supersedes: r12, r14

## Scope

Files this will change:
- src/one.py — first

## Verdict

Patch, because it is small.

## Disagreement

None.

## Budget

~4 files, ~120 lines.
"""


def _session(files: int, lines: int) -> dict:
    return {"files_touched": [f"f{i}.py" for i in range(files)], "lines_changed": lines}


def test_a_plan_that_matched_its_forecast_held():
    assert session_end.outcome_for(Contract(BUDGETED), _session(4, 120)) == "held"
    assert session_end.outcome_for(Contract(BUDGETED), _session(5, 150)) == "held"


def test_a_plan_whose_session_overran_it_is_recorded_as_reworked():
    """The signature this whole flow exists to make visible: a plan predicting
    four files and 120 lines, executed as nine files and 900."""
    assert session_end.outcome_for(Contract(BUDGETED), _session(9, 900)) == "reworked"
    # Either overrun alone is enough — scope drift and churn are both rework.
    assert session_end.outcome_for(Contract(BUDGETED), _session(8, 100)) == "reworked"
    assert session_end.outcome_for(Contract(BUDGETED), _session(4, 400)) == "reworked"


def test_a_plan_with_no_forecast_is_open_not_held():
    """Unmeasured is not the same as fine. Recording it as `held` would put a
    fact in the roadmap that nothing established."""
    assert session_end.outcome_for(Contract(WRAPPED), _session(9, 900)) == "open"
    assert session_end.outcome_for(Contract(BUDGETED), _session(0, 0)) == "open"


def test_a_plan_names_what_it_supersedes():
    assert Contract(BUDGETED).supersedes == ["r12", "r14"]
    assert Contract(WRAPPED).supersedes == []


def test_the_forecast_is_read_off_the_budget_line():
    assert Contract(BUDGETED).budget == (4, 120)
    assert Contract(WRAPPED).budget is None


def test_a_heading_with_a_qualifier_is_still_read(tmp_path, monkeypatch):
    """`_section` required the heading line to be exactly `## Verdict`, so a plan
    that wrote `## Verdict — patch` produced an empty section.

    When every section comes back empty `entry_for` returns None, `write_roadmap`
    returns early, and the session's decisions are never recorded — inside a hook
    wrapped in `except Exception: pass`, with nothing to distinguish it from a
    session that had no approved contract. The memory silently stops accumulating
    and the next plan re-covers the same ground.
    """
    qualified = (
        WRAPPED.replace("## Verdict", "## Verdict — the call and why")
        .replace("## Scope", "## Scope (files this will change)")
        .replace("## Disagreement", "## Disagreement — where I pushed back")
    )

    derived = session_end.entry_for(Contract(qualified))

    assert derived is not None, "a qualified heading emptied the whole entry"
    title, body = derived
    assert "decided:" in body, body
    assert "deferred:" in body, "the `Explicitly NOT changing` bullets were lost"


def test_a_new_plan_is_recorded_even_when_an_older_body_mentions_its_title(
    tmp_path, monkeypatch
):
    """The duplicate check was `if title in roadmap.read(root)` — a substring test
    against the whole file, bodies included.

    So a plan titled "Turn the gates back on" was silently dropped if any earlier
    entry merely *mentioned* that phrase, which is the ordinary case for
    follow-up work on the same area. The roadmap then looked as though it had
    already covered ground it had only talked about, and the session that did the
    work recorded nothing at all.
    """
    session_end.roadmap.append(
        tmp_path,
        "Earlier unrelated decision",
        "- deferred: revisit whether to Turn the gates back on for CI",
    )
    monkeypatch.setattr(session_end.contract_mod, "load", lambda _: Contract(WRAPPED))

    session_end.write_roadmap({"session_id": "s", "repo_root": str(tmp_path)})

    titles = [e.title for e in session_end.roadmap.entries(tmp_path)]
    assert "Turn the gates back on" in titles, titles
