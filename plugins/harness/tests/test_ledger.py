"""What the ledger can see, which is the whole of what the report can say.

The defect these were written for: `read_transcript` read one file, and every
subagent wrote somewhere else. So the plugin's central economic claim — that
delegating to a cheaper model is worth it — was the one figure it could not
produce, and a real day showed $603 against Opus and nothing at all against
Sonnet while Sonnet had in fact run 77 turns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import ledger


def write_transcript(path: Path, turns: list[tuple[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for model, output in turns:
        lines.append(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "model": model,
                        "usage": {"input_tokens": 10, "output_tokens": output},
                    }
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def session_with_subagents(tmp_path: Path) -> Path:
    """The real on-disk layout: subagents live beside the session, under it."""
    main = tmp_path / "sess.jsonl"
    write_transcript(main, [("claude-opus-5", 1000)])
    subagents = tmp_path / "sess" / "subagents"
    write_transcript(subagents / "agent-a.jsonl", [("claude-sonnet-5", 500)])
    write_transcript(subagents / "agent-b.jsonl", [("claude-sonnet-5", 500)])
    return main


def test_cost_of_pins_the_multipliers_not_just_the_ordering(tmp_path):
    """`CACHE_READ_MULTIPLIER` etc. are the headline spend figure's arithmetic.

    Earlier tests here only assert cost is positive and ordered, so setting
    `CACHE_READ_MULTIPLIER = 1.0` — a 10x error in the number the report leads
    with — survives them silently. Pin the actual result for a known usage
    block against a known rate, so a changed multiplier is a failing test
    rather than a quietly wrong headline.
    """
    # Four distinct magnitudes, deliberately. With all four inputs equal the
    # billable total is a plain sum of the multipliers, so it is unchanged by
    # permuting which multiplier applies to which input — swapping the 5m and 1h
    # rates, or reading each from the other's usage key, left the test green
    # while a 1h cache write billed at the 5m rate under-reports that component
    # by 37.5%. Distinct magnitudes make every such swap move the number.
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_input_tokens": 10_000_000,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 4_000_000,
            "ephemeral_1h_input_tokens": 2_000_000,
        },
    }

    cost = ledger.cost_of("claude-opus-5", usage)

    # claude-opus-5 is $5.00/M in, $25.00/M out. Billable input tokens are
    # 1M plain + 10M * 0.1 (cache read) + 4M * 1.25 (5m write) + 2M * 2.0 (1h
    # write) = 1 + 1 + 5 + 4 = 11M, so cost = 11 * 5.00 + 1 * 25.00 = 80.00.
    assert cost == pytest.approx(80.00)


def test_subagent_spend_is_counted(tmp_path):
    """Without this the report says delegation costs nothing at all."""
    main = session_with_subagents(tmp_path)

    usage = ledger.read_transcript(str(main))

    assert usage["subagents"]["count"] == 2
    assert usage["subagents"]["cost_usd"] > 0
    assert usage["total_cost_usd"] > usage["cost_usd"]


def test_the_lead_and_its_subagents_stay_separate(tmp_path):
    """A single merged figure cannot answer the question the report is for.

    If subagent turns leaked into the lead's own bucket, `cost_usd` would
    silently mean something different from what every earlier entry means.
    """
    main = session_with_subagents(tmp_path)

    usage = ledger.read_transcript(str(main))

    assert usage["models"].keys() == {"claude-opus-5"}
    assert usage["subagents"]["models"].keys() == {"claude-sonnet-5"}
    assert usage["assistant_turns"] == 1
    assert usage["subagents"]["assistant_turns"] == 2


def test_a_session_with_no_subagents_still_reports(tmp_path):
    main = tmp_path / "sess.jsonl"
    write_transcript(main, [("claude-opus-5", 100)])

    usage = ledger.read_transcript(str(main))

    assert usage["subagents"]["count"] == 0
    assert usage["total_cost_usd"] == usage["cost_usd"]


def test_the_summary_counts_delegated_spend(tmp_path):
    """The number the user actually reads."""
    main = session_with_subagents(tmp_path)
    usage = ledger.read_transcript(str(main))

    summary = ledger.summarize([{"usage": usage, "lines_changed": 1, "checks_run": 0}])

    assert "claude-sonnet-5" in summary
    assert "delegated to subagents" in summary
    # The label is a literal in the format string, so asserting on it alone
    # passes with the figure hardcoded to zero — which is the whole number the
    # report exists to show.
    # Anchored to the label. The bare figure also appears in the per-model
    # table, so asserting on it alone passes with the delegated total zeroed.
    delegated = usage["subagents"]["cost_usd"]
    assert f"delegated to subagents: ${delegated:8.2f}" in summary, summary
    assert f"${usage['total_cost_usd']:.2f} total" in summary


def test_a_rebuilt_entry_does_not_report_zero_checks_as_a_measurement():
    """`or 0` turned "never recorded" into "ran, found nothing".

    A plugin reinstall deleted the ledger; cost was rebuilt from transcripts,
    but gate counts come from hook execution and could not be. Reporting those
    as `0 run` is the confident wrong number this file's own skill forbids.
    """
    rebuilt = {"usage": {"cost_usd": 1.0, "total_cost_usd": 1.0}, "checks_run": None}
    measured = {"usage": {"cost_usd": 1.0, "total_cost_usd": 1.0}, "checks_run": 4,
                "checks_failed": 1, "contract": True}

    summary = ledger.summarize([rebuilt, measured])

    assert "4 run, 1 blocked" in summary
    assert "of 1 sessions" in summary, "the denominator must exclude unmeasured rows"
    assert "unknown for 1 session" in summary


def test_an_entry_written_before_subagents_were_counted_still_reads():
    """Old rows have no `total_cost_usd`; reporting them as free would be worse
    than reporting them as incomplete."""
    old = {"usage": {"models": {"claude-opus-5": {"cost_usd": 5.0}}, "cost_usd": 5.0}}

    summary = ledger.summarize([old])

    assert "$5.00" in summary


def _session(turns: int, files: int, lines: int, contract: bool = False) -> dict:
    return {
        "usage": {"cost_usd": 1.0, "total_cost_usd": 1.0, "assistant_turns": turns},
        "files_touched": files,
        "lines_changed": lines,
        "checks_run": 0,
        "contract": contract,
    }


def test_rework_separates_sessions_by_how_long_they_ran():
    """The number the 0.8 flow exists to move.

    Lines changed *per file touched* is not a size measure. A session that
    touches nine files at four hundred lines apiece did not write four hundred
    lines nine times — it wrote a hundred and rewrote them, because the design
    changed while it was being built. That figure rising with session length is
    the whole diagnosis.
    """
    rows = dict(
        (name, ratio)
        for name, _, ratio, _ in ledger.rework(
            [_session(20, 2, 20), _session(300, 3, 400), _session(900, 4, 540)]
        )
    )

    assert rows["short"] == 10
    assert rows["long"] == pytest.approx(133.3, abs=0.1)
    assert rows["marathon"] == 135


def test_rework_pools_a_bucket_rather_than_averaging_its_ratios():
    """A session that touched one file and rewrote it once, next to one that
    touched twenty. Averaging the two ratios lets the small session count as
    much as the large one and hides the churn that costs money."""
    rows = {name: ratio for name, _, ratio, _ in ledger.rework([_session(300, 1, 10), _session(300, 19, 1990)])}

    assert rows["long"] == 100, "20 files and 2000 lines is 100, not the mean of 10 and 105"


def test_a_session_that_changed_nothing_has_no_ratio():
    """`lines / files` with no files is a crash, and counting it as zero drags
    the figure down with sessions that never wrote anything."""
    assert ledger.rework([_session(300, 0, 0)]) == []


def test_rework_reports_how_many_of_each_bucket_were_planned():
    """The second scoreboard number. If the flow still does not run, the first
    one cannot move — the flow is the only thing that writes the memory the
    whole design depends on."""
    rows = {name: planned for name, _, _, planned in ledger.rework(
        [_session(300, 1, 10, contract=True), _session(300, 1, 10), _session(300, 1, 10),
         _session(300, 1, 10)]
    )}

    assert rows["long"] == 25


def test_the_summary_prints_the_rework_table_and_says_what_it_means():
    """A reading with no rule attached measurably fails to change behaviour, and
    there is no skill left to supply the rule in prose."""
    summary = ledger.summarize([_session(900, 4, 540)])

    assert "lines changed per file touched" in summary
    assert "marathon" in summary
    assert "design changing while it is being built" in summary


def test_the_summary_states_what_it_cannot_tell_you():
    """A session resumed several times is recorded once per SessionEnd, each a
    fresh re-read of the whole transcript, so its cost is counted that many
    times. This was in the report skill's prose; the skill is gone, so the
    number has to carry its own caveat or it reads as exact."""
    summary = ledger.summarize([_session(20, 1, 10)])

    assert "resumed several times" in summary
    assert "upper bound" in summary


# ------------------------------------------- an unread transcript is not free


def test_a_transcript_that_cannot_be_read_is_marked_rather_than_counted_as_zero(tmp_path):
    """Nothing read and nothing spent are the same arithmetic and opposite facts.

    Every figure the report prints is a sum over these entries, so a session
    whose transcript could not be opened contributes $0 to the total, 0 turns to
    the length bucket, and reads as a cheap session that went well.
    """
    totals = ledger.read_transcript(str(tmp_path / "gone.jsonl"))

    assert totals["cost_usd"] == 0
    assert totals["incomplete"] is True, "an unread transcript is indistinguishable from a free session"


def test_a_transcript_that_reads_cleanly_is_not_marked(tmp_path):
    """The other direction, so the flag cannot pass by always being set — which
    would move every real session out of the rework table."""
    path = tmp_path / "s.jsonl"
    write_transcript(path, [("claude-opus-4-1", 1000)])

    assert "incomplete" not in ledger.read_transcript(str(path))


def test_the_subagent_scan_stops_on_its_deadline_and_says_so(tmp_path):
    """`SessionEnd` hooks share a 1.5s budget and a plugin's own `timeout` cannot
    raise it, so overrunning means the process is killed — losing the ledger line
    and the lessons harvested beside it. Stopping early and saying so keeps both.
    """
    path = tmp_path / "s.jsonl"
    write_transcript(path, [("claude-opus-4-1", 1000)])
    subagents = tmp_path / "s" / "subagents"
    for i in range(3):
        write_transcript(subagents / f"agent-{i}.jsonl", [("claude-sonnet-4-5", 500)])

    totals = ledger.read_transcript(str(path), budget=-1)

    assert totals["incomplete"] is True
    assert totals["subagents"]["count"] == 0, "counted subagents it never opened"


def test_an_unpriced_session_is_left_out_of_the_rework_table():
    """The rework figure is the one number the whole 0.8 flow exists to move, so
    it is the one most worth protecting from a silent zero: an unread transcript
    has no turn count, and bucketing it anyway files a nine-hundred-turn marathon
    under `short`."""
    real = {"files_touched": 2, "lines_changed": 400, "usage": {"assistant_turns": 900}}
    unread = {"files_touched": 2, "lines_changed": 400, "usage": {"incomplete": True}}

    buckets = {name for name, *_ in ledger.rework([real, unread])}

    assert buckets == {"marathon"}, f"an unpriced session was bucketed by a turn count it does not have: {buckets}"


def test_the_report_says_when_the_totals_are_only_a_floor():
    """Otherwise a total summed over sessions that contributed nothing reads as a
    measurement."""
    out = ledger.summarize([
        {"files_touched": 1, "lines_changed": 10, "checks_run": 1, "usage": {"cost_usd": 5.0}},
        {"files_touched": 1, "lines_changed": 10, "checks_run": 1, "usage": {"incomplete": True}},
    ])

    assert "lower bound" in out, "an unpriced session was folded into the total in silence"
