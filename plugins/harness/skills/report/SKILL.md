---
name: report
description: Show what recent sessions cost, which models the money went to, and how often the gates caught something. Use when asking whether the harness is worth it, where token spend is going, or whether a cheaper model would have worked.
model: haiku
allowed-tools: Bash, Read
---

# What has this actually cost?

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py"
```

Present the numbers plainly, **and say what they miss.** Cost comes from real
token counts in each session's transcript rather than an estimate, and it is
split two ways: the lead session, and everything delegated to subagents — the
reviewers, the judgement agents and the workers. The report prints that as
`Lead session: $X   delegated to subagents: $Y`. Keep the split when you relay
it, because the question the ledger exists to answer is whether delegating to a
cheaper model pays, and a single merged figure cannot answer it.

What it still gets wrong: it appends one entry per `SessionEnd`, each a fresh
re-read of the transcript, so a session resumed several times is summed that
many times over. Say so when you give a total. Entries written before subagents
were counted carry only the lead's cost and are reported as such rather than as
free.

The split it does report — including the cache-read and cache-write split,
which is usually where most of the money is.

## Reading it honestly

The number that matters is not the total. It is **how often a check caught
something**. Each blocked edit is a defect that was fixed inside the turn that
created it, instead of surviving to the end and costing another round trip to
find. If that rate is near zero, either the code is genuinely clean or the gates
are not detecting anything useful — and the second possibility deserves saying
out loud rather than reporting a flattering total.

Also worth naming when it shows up:

- **One model doing all the work.** If nearly every dollar is on Opus, the
  phase split is not happening — implementation is meant to run cheaper because
  the contract makes the task precise and the gates verify it.
- **Sessions with no contract.** Compare their line counts against sessions
  that had one. Large unplanned changes are where scope creep and rework live.
- **Cache reads dwarfing fresh input.** That is normal and cheap. Do not present
  it as waste — cache reads bill at about a tenth of the input rate.

## Answering "would a cheaper model have worked?"

Answer from the ledger's phase split, not from a synthetic run — and be honest
about what it can and cannot settle.

What it can settle: whether the phase split is happening at all. If the
"delegated to subagents" figure is near zero, every task is running on the
lead's model regardless of what the agents declare, and that is worth fixing
before asking about cheaper models at all. If it is non-trivial, check which
agents are eating it — `report`'s own split by model shows whether Opus work
(`architect`, the correctness/security/tests reviewers, `refuter`) is
proportionate to how rarely those agents should run.

What it cannot settle: whether a *specific* agent would do as well on a
cheaper model. The ledger records what happened, not what would have happened
on a different model — that needs an actual run on that model, compared
against real outcomes (did the gates catch the same things, did review find
the same issues), not a synthetic benchmark.

Also worth saying plainly, because it bounds how much this question is worth
chasing: measured over 211 sessions, 83.5% of lifetime spend is context on the
lead session, and subagents are only 6.3% of it. Even a perfect swap of every
agent to a cheaper model caps out near 2% of total cost. The lever that
actually moves the number is context management on the lead session — an
`autoCompactWindow` setting, not a model choice.
