---
name: report
description: Show what recent sessions cost, which models the money went to, and how often the gates caught something. Use when asking whether the harness is worth it, where token spend is going, or whether a cheaper model would have worked.
model: haiku
allowed-tools: Bash, Read
---

# What has this actually cost?

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ledger.py"
```

Present the numbers plainly. Cost comes from real token counts in each session's
transcript, not an estimate — including the cache-read and cache-write split,
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

The ledger cannot answer that on its own, and it should not pretend to. What
answers it is an A/B against the same cases:

```bash
claude plugin eval "${CLAUDE_PLUGIN_ROOT}" --ablation with-without --model claude-sonnet-5
claude plugin eval "${CLAUDE_PLUGIN_ROOT}" --ablation with-without --model claude-opus-5
```

Each run scores the same cases with the plugin and without it. If Sonnet with
the harness scores at or above Opus without it, the plugin has paid for itself
and the default model can change. If it has not, say so — the point of measuring
is to be able to be wrong.
