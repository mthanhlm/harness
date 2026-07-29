#!/usr/bin/env python3
"""Measure what sessions actually cost and whether the gates earned their keep.

The user's sharpest complaint was economic: Opus is expensive, Sonnet is cheap
and fast but does not land the task, so they pay for Opus by default. The whole
plugin is a bet that a cheaper model succeeds once it has a precise contract and
checks it can run. A bet needs a scoreboard, so this reads real token counts out
of the session transcript rather than estimating them.

Rates are list prices per million tokens. Cache reads bill at roughly a tenth of
the input rate; cache writes at 1.25x for the five-minute TTL and 2x for the
one-hour one. The transcript records those two buckets separately, so the cost
here is computed rather than approximated.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from state import ledger_dir

# USD per million tokens, input and output.
RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
}
FALLBACK_RATE = (5.00, 25.00)

CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_WRITE_1H_MULTIPLIER = 2.0

MILLION = 1_000_000


def _normalize(model: str) -> str:
    """Strip context-window and date suffixes, e.g. `claude-opus-5[1m]`."""
    base = model.split("[", 1)[0].strip()
    if base in RATES:
        return base
    # Dated snapshots such as claude-haiku-4-5-20251001.
    for known in RATES:
        if base.startswith(known):
            return known
    return base


def cost_of(model: str, usage: dict[str, Any]) -> float:
    rate_in, rate_out = RATES.get(_normalize(model), FALLBACK_RATE)

    plain_in = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)

    creation = usage.get("cache_creation")
    if isinstance(creation, dict):
        write_5m = int(creation.get("ephemeral_5m_input_tokens") or 0)
        write_1h = int(creation.get("ephemeral_1h_input_tokens") or 0)
    else:
        # Older transcripts only carry the total; assume the cheaper TTL rather
        # than inflating the number we show the user.
        write_5m = int(usage.get("cache_creation_input_tokens") or 0)
        write_1h = 0

    billable_in = (
        plain_in
        + cache_read * CACHE_READ_MULTIPLIER
        + write_5m * CACHE_WRITE_5M_MULTIPLIER
        + write_1h * CACHE_WRITE_1H_MULTIPLIER
    )
    return (billable_in / MILLION) * rate_in + (out / MILLION) * rate_out


def _empty_totals() -> dict[str, Any]:
    return {"models": {}, "cost_usd": 0.0, "assistant_turns": 0}


def _accumulate(path: Path, totals: dict[str, Any]) -> dict[str, Any]:
    """Add one transcript's usage to `totals`, in place."""
    try:
        handle = path.open(encoding="utf-8")
    except OSError:
        return totals

    with handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue

            model = message.get("model") or "unknown"
            entry = totals["models"].setdefault(
                model,
                {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0.0},
            )
            entry["input"] += int(usage.get("input_tokens") or 0)
            entry["output"] += int(usage.get("output_tokens") or 0)
            entry["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
            entry["cache_write"] += int(usage.get("cache_creation_input_tokens") or 0)

            cost = cost_of(model, usage)
            entry["cost_usd"] += cost
            totals["cost_usd"] += cost
            totals["assistant_turns"] += 1
    return totals


def subagent_transcripts(path: str) -> list[Path]:
    """Where a session's subagents wrote, given the session's own transcript.

    Claude Code puts them in a directory named for the session, beside the
    session file: `<project>/<session-id>/subagents/agent-*.jsonl`. Nothing in
    the main transcript records them — there are no sidechain turns in it — so a
    ledger that reads one file sees only the lead.

    """
    main = Path(path)
    directory = main.parent / main.stem / "subagents"
    try:
        return sorted(p for p in directory.glob("*.jsonl") if p.is_file())
    except OSError:
        return []


def read_transcript(path: str) -> dict[str, Any]:
    """Sum token usage and cost per model for a session, lead and subagents.

    The two are kept apart rather than merged. Knowing the total matters, but
    the question the report exists to answer — is delegating to a cheap model
    worth it — needs the split, and a single figure cannot be un-added later.
    """
    totals = _accumulate(Path(path), _empty_totals())

    delegated = _empty_totals()
    files = subagent_transcripts(path)
    for transcript in files:
        _accumulate(transcript, delegated)
    delegated["count"] = len(files)

    totals["subagents"] = delegated
    totals["total_cost_usd"] = totals["cost_usd"] + delegated["cost_usd"]
    return totals


def record(session: dict[str, Any], transcript_path: str | None) -> dict[str, Any]:
    """Append one session summary to the ledger."""
    import contract as contract_mod

    usage = read_transcript(transcript_path) if transcript_path else {}
    checks = session.get("checks") or {}

    # The contract lives in its own file rather than in session state, so read
    # it here — otherwise the report would claim every session was unplanned.
    agreed = contract_mod.load(session.get("session_id", "unknown"))
    entry = {
        "session_id": session.get("session_id"),
        "repo": session.get("repo_root"),
        "files_touched": len(session.get("files_touched") or []),
        "lines_changed": session.get("lines_changed") or 0,
        "checks_run": checks.get("run", 0),
        "checks_failed": checks.get("failed", 0),
        "stop_blocks": session.get("consecutive_stop_blocks") or 0,
        "contract": bool(agreed and agreed.approved),
        "verdict": agreed.verdict if agreed else None,
        "usage": usage,
    }
    path = ledger_dir() / "sessions.jsonl"
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError:
        pass
    return entry


def load_all() -> list[dict[str, Any]]:
    entries = []
    path = ledger_dir() / "sessions.jsonl"
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


def summarize(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "No sessions recorded yet. The ledger fills as you work with the harness on."

    # `total_cost_usd` is absent from entries written before subagents were
    # counted; falling back to the lead's own cost keeps those readable rather
    # than reporting them as free.
    def entry_cost(entry: dict[str, Any]) -> float:
        usage = entry.get("usage") or {}
        if usage.get("total_cost_usd") is not None:
            return float(usage["total_cost_usd"])
        return float(usage.get("cost_usd") or 0)

    total_cost = sum(entry_cost(e) for e in entries)
    lead_cost = sum(float((e.get("usage") or {}).get("cost_usd") or 0) for e in entries)
    delegated_cost = total_cost - lead_cost

    per_model: dict[str, float] = {}
    for entry in entries:
        usage = entry.get("usage") or {}
        buckets = [usage.get("models") or {}, (usage.get("subagents") or {}).get("models") or {}]
        for models in buckets:
            for model, stats in models.items():
                per_model[model] = per_model.get(model, 0.0) + float(stats.get("cost_usd") or 0)

    # Gate counts come from hook execution, so an entry rebuilt from a
    # transcript has none — and `or 0` would report "0 checks run" as a
    # measurement rather than an absence. Those rows are counted separately and
    # excluded from the denominator instead.
    measured = [e for e in entries if e.get("checks_run") is not None]
    unmeasured = len(entries) - len(measured)
    checks_run = sum(int(e.get("checks_run") or 0) for e in measured)
    checks_failed = sum(int(e.get("checks_failed") or 0) for e in measured)
    lines = sum(int(e.get("lines_changed") or 0) for e in entries)
    contracts = sum(1 for e in measured if e.get("contract"))

    out = [
        f"{len(entries)} sessions, ~{lines} lines changed, ${total_cost:.2f} total",
        "",
        "By model:",
    ]
    for model, cost in sorted(per_model.items(), key=lambda kv: -kv[1]):
        share = (cost / total_cost * 100) if total_cost else 0
        out.append(f"  {model:28} ${cost:8.2f}  ({share:.0f}%)")

    out += [
        "",
        f"Lead session: ${lead_cost:8.2f}   delegated to subagents: ${delegated_cost:8.2f}",
        "",
    ]
    if measured:
        out += [
            f"Per-edit checks: {checks_run} run, {checks_failed} caught a problem the edit introduced",
            f"Contracts agreed before coding: {contracts} of {len(measured)} sessions",
        ]
    if unmeasured:
        out.append(
            f"Gate counts unknown for {unmeasured} session(s) rebuilt from transcripts —"
            " cost is real, the check figures were never recorded."
        )
    if checks_run:
        rate = checks_failed / checks_run * 100
        out.append(
            f"A check blocked {rate:.1f}% of the time — each one is a defect that did not"
            " reach the end of the turn."
        )
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "transcript":
        print(json.dumps(read_transcript(sys.argv[2]), indent=2))
    else:
        print(summarize(load_all()))
