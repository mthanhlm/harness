"""Harvesting lessons from the contract, and the outcome arithmetic beside it.

This is the only part of the plugin that writes durable project memory, and it
runs inside a `SessionEnd` hook wrapped in `except Exception: pass` — so a total
failure is indistinguishable from "no approved contract", with nobody watching.

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

## Verdict

Patch. The architect read the code and ran it against real data: 138 passing
tests, every module states its contract, and each fix lands on an existing
seam. Nothing here justifies a rewrite.

## Disagreement

None.

## Lessons

- Releasing this plugin takes two commands: `claude plugin marketplace
  update` refreshes the checkout, and installing the new version is a
  separate step.
- A rejected proposal should not be re-proposed without new evidence.

## Risks

Something.
"""


def test_a_lesson_bullet_is_split_into_title_and_elaboration():
    """The bug a truncated read would produce: a bullet cut mid-clause reads
    as a title with no body, or a body with the wrong title."""
    bullets = session_end._lessons_bullets(WRAPPED)

    assert len(bullets) == 2
    title, body, retires = session_end._split_lesson(bullets[0])
    assert retires is None
    assert title == "Releasing this plugin takes two commands"
    assert body.startswith("`claude plugin marketplace update` refreshes")
    assert "  " not in body, "continuation lines must be joined on single spaces"


def test_a_bullet_with_no_colon_still_carries_something():
    title, body, retires = session_end._split_lesson(
        "A rejected proposal should not be re-proposed without new evidence."
    )

    assert retires is None
    assert title == body
    assert title


def test_a_contract_with_no_lessons_section_writes_nothing(tmp_path, monkeypatch):
    """Silence is correct: most sessions teach nothing durable."""
    bare = WRAPPED.split("## Lessons")[0] + "## Risks\nSomething.\n"
    monkeypatch.setattr(session_end.contract_mod, "load", lambda _: Contract(bare))
    written = []
    monkeypatch.setattr(session_end.lessons, "append", lambda *a: written.append(a))

    session_end.write_lessons({"session_id": "s", "repo_root": str(tmp_path)})

    assert written == []


def test_an_unapproved_contract_writes_nothing(tmp_path, monkeypatch):
    """A pending plan has not agreed to anything yet, lessons included."""
    pending = WRAPPED.replace("status: approved", "status: pending")
    monkeypatch.setattr(session_end.contract_mod, "load", lambda _: Contract(pending))
    written = []
    monkeypatch.setattr(session_end.lessons, "append", lambda *a: written.append(a))

    session_end.write_lessons({"session_id": "s", "repo_root": str(tmp_path)})

    assert written == []


def test_the_lessons_section_is_recorded_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(session_end.contract_mod, "load", lambda _: Contract(WRAPPED))

    session_end.write_lessons({"session_id": "s", "repo_root": str(tmp_path)})

    titles = [e.title for e in session_end.lessons.entries(tmp_path)]
    assert "Releasing this plugin takes two commands" in titles
    assert any("re-proposed without new evidence" in t for t in titles)


def test_the_same_session_does_not_stack_duplicate_lessons(tmp_path, monkeypatch):
    """`SessionEnd` can fire more than once for one session; harvesting twice
    must not double the file."""
    monkeypatch.setattr(session_end.contract_mod, "load", lambda _: Contract(WRAPPED))
    session = {"session_id": "s", "repo_root": str(tmp_path)}

    session_end.write_lessons(session)
    first = session_end.lessons.read(tmp_path)
    session_end.write_lessons(session)

    assert session_end.lessons.read(tmp_path) == first
    assert first.count("Releasing this plugin takes two commands") == 1


def test_a_heading_with_a_qualifier_is_still_read():
    """`_section` required the heading line to be exactly `## Lessons`, so a
    plan that wrote `## Lessons learned` would have produced an empty section
    and the harvest below it would find nothing."""
    qualified = WRAPPED.replace("## Lessons", "## Lessons learned this session")

    bullets = session_end._lessons_bullets(qualified)

    assert bullets, "a qualified heading emptied the section"


def test_lessons_written_as_plain_lines_are_still_harvested():
    """The skill says "one line each" and neither of its examples carries a dash,
    so this is what following the instruction exactly produces. Collecting
    nothing from it was silent twice over: `write_lessons` returns early, and its
    caller swallows exceptions, so it looked like a session that learned nothing.
    """
    plain = WRAPPED.split("## Lessons")[0] + (
        "## Lessons\n"
        "Releasing takes two commands: the first installs nothing.\n"
        "A version bump is load-bearing: without one the update is a no-op.\n"
    )

    bullets = session_end._lessons_bullets(plain)

    assert len(bullets) == 2, bullets
    assert bullets[0].startswith("Releasing takes two commands")


def test_an_unfilled_template_placeholder_is_not_filed_as_a_lesson():
    """Every section of the contract template describes itself inside angle
    brackets. Harvesting one would put the instructions for writing a lesson on
    file as a lesson, in the one place meant to hold only what was learned."""
    left = WRAPPED.split("## Lessons")[0] + (
        "## Lessons\n<Optional. What this session learned that will still be\n"
        "true in three months.>\n"
    )

    assert session_end._lessons_bullets(left) == []


def test_two_lessons_that_open_with_the_same_clause_are_both_kept(tmp_path, monkeypatch):
    """`_split_lesson` cuts at the first `": "`, so the title is only the opening
    clause — and an opening clause names the thing the lesson is about, which two
    lessons about one thing will share. Keying the duplicate guard on it alone
    dropped the second and left the first reading as the whole harvest."""
    pair = WRAPPED.split("## Lessons")[0] + (
        "## Lessons\n"
        "- Releasing: the marketplace command installs nothing.\n"
        "- Releasing: verify from installed_plugins.json, never a success message.\n"
    )
    monkeypatch.setattr(session_end.contract_mod, "load", lambda _: Contract(pair))

    session_end.write_lessons({"session_id": "s", "repo_root": str(tmp_path)})

    bodies = [e.body for e in session_end.lessons.entries(tmp_path)]
    assert len(bodies) == 2, bodies


def test_a_superseding_bullet_corrects_the_old_lesson_instead_of_adding_one(tmp_path, monkeypatch):
    """The learn-and-update mechanism, end to end and without a human step.

    The old lesson must survive, marked; the new one must carry the pointer back.
    A version that quietly overwrote would be indistinguishable from one that had
    never been wrong, which is the property this whole file exists to keep.
    """
    monkeypatch.setattr(session_end.contract_mod, "load", lambda _: Contract(WRAPPED))
    session_end.write_lessons({"session_id": "s", "repo_root": str(tmp_path)})
    original = [e for e in session_end.lessons.entries(tmp_path) if e.title.startswith("Releasing")][0]

    correction = WRAPPED.split("## Lessons")[0] + (
        f"## Lessons\n- supersedes {original.id}: Releasing takes three commands"
        " now, and the last one is a restart.\n"
    )
    monkeypatch.setattr(session_end.contract_mod, "load", lambda _: Contract(correction))
    session_end.write_lessons({"session_id": "s2", "repo_root": str(tmp_path)})

    by_id = {e.id: e for e in session_end.lessons.entries(tmp_path)}
    assert by_id[original.id].body == original.body, "the corrected lesson lost its original text"
    replacement = by_id[by_id[original.id].superseded_by]
    assert replacement.supersedes == original.id
    assert replacement.title.startswith("Releasing takes three commands")


def _session(files: int, lines: int) -> dict:
    return {"files_touched": [f"f{i}.py" for i in range(files)], "lines_changed": lines}


BUDGETED = """# Plan: A modest change

status: approved
verdict: patch

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
    fact on record that nothing established."""
    assert session_end.outcome_for(Contract(WRAPPED), _session(9, 900)) == "open"
    assert session_end.outcome_for(Contract(BUDGETED), _session(0, 0)) == "open"


def test_a_bullet_can_declare_which_lesson_it_retires():
    """The declaration rides on the bullet, not in a header at the top of the
    contract. A header names which entry dies but never which text replaces it,
    so the pairing had to be reconstructed by hand at the end of a long turn —
    and this plugin's own record is that such a step never gets taken."""
    title, body, retires = session_end._split_lesson(
        "supersedes L3: Releasing takes two commands, not one."
    )

    assert retires == "L3"
    assert title == "Releasing takes two commands, not one."
    assert not title.lower().startswith("supersedes")


def test_the_forecast_is_read_off_the_budget_line():
    assert Contract(BUDGETED).budget == (4, 120)
    assert Contract(WRAPPED).budget is None


def _recorded(monkeypatch, contract_text: str | None, session: dict) -> dict:
    """Run `main` far enough to see the ledger entry, without writing one."""
    captured = {}
    loaded = Contract(contract_text) if contract_text is not None else None
    monkeypatch.setattr(session_end.contract_mod, "load", lambda _: loaded)
    monkeypatch.setattr(session_end, "read_event", lambda: {"session_id": "s"})
    monkeypatch.setattr(session_end, "load_session", lambda _: session)
    monkeypatch.setattr(session_end, "write_lessons", lambda _: None)
    monkeypatch.setattr(
        session_end, "record", lambda s, t, outcome=None: captured.update(outcome=outcome)
    )
    session_end.main()
    return captured


def test_the_plans_outcome_reaches_the_ledger(monkeypatch):
    """`outcome_for` sat uncalled while the roadmap owned the verdict, so
    deleting the roadmap would have deleted the one number here that nobody has
    to trust a model to self-report. This is the test that would have caught it
    still being uncalled.
    """
    assert _recorded(monkeypatch, BUDGETED, _session(9, 900))["outcome"] == "reworked"
    assert _recorded(monkeypatch, BUDGETED, _session(4, 120))["outcome"] == "held"


def test_a_session_nobody_planned_is_judged_by_nothing(monkeypatch):
    """`None`, not `held`. An unapproved or absent plan forecast nothing anyone
    agreed to, and a verdict no decision backs is exactly the kind of measured-
    looking absence this ledger exists to keep out."""
    pending = BUDGETED.replace("status: approved", "status: pending")
    assert _recorded(monkeypatch, pending, _session(9, 900))["outcome"] is None
    assert _recorded(monkeypatch, None, _session(9, 900))["outcome"] is None
