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


def test_an_entry_written_before_subagents_were_counted_still_reads():
    """Old rows have no `total_cost_usd`; reporting them as free would be worse
    than reporting them as incomplete."""
    old = {"usage": {"models": {"claude-opus-5": {"cost_usd": 5.0}}, "cost_usd": 5.0}}

    summary = ledger.summarize([old])

    assert "$5.00" in summary
