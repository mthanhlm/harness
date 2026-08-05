> LLM and agent system judgement — context economics, KV-cache discipline, prompt
> and tool design, memory that does not rot, multi-agent decisions, and the
> failure modes that only appear in production. Loads on prompt, agent, skill and
> tool-definition files, and anywhere an LLM SDK is used.
>
> Domain: LLM and agent systems

# LLM and agent systems lens

The frame everything here sits in:

> **Agent = LLM + [Context + Tools + Constrain + Verify + Correct] = Model + Harness**

You do not control the model. Everything in the brackets is yours, and it is
where the difference between a demo and a system lives.

## Context is a budget, not a container

The window has two distinct failure modes, and they need different fixes.

- **Overflow** — it does not fit. Obvious; you get an error.
- **Rot** — it fits, and the model cannot find what matters in it. Silent, and it
  worsens as the window fills.

Rot is the expensive one. A model given 200k tokens of mostly-irrelevant context
performs worse than the same model given 20k of relevant context, and nothing in
the output says so.

**The consequence: information density beats window size.** Every token in the
context should be earning its place.

| Instead of | Do |
|---|---|
| Pinning reference docs in the system prompt | Load on demand; keep names and one-line descriptions in the prefix |
| Reading twelve files into the main loop | Send a subagent; it returns a conclusion, not its reading |
| A 2000-line instruction file | The important lines get ignored *because* of the unimportant ones |
| Dumping a whole API response | Extract the fields the next step needs |
| Keeping every tool result forever | Compact old ones; keep the decisions, drop the raw output |

**Tell in review**: a system prompt that grew by 300 lines with nothing removed;
a tool returning an entire file when the caller needed one function.

## KV cache: the cheapest optimisation available

Cache reads bill at roughly a tenth of the input rate. Whether you get them is
decided by one property:

> **The context must be a stable prefix that only grows at the end.**

Causal attention means each token's key/value pairs depend only on the tokens
before it. Append at the end → everything before stays valid. Change anything
early → every token after it is recomputed.

| Breaks the cache | Instead |
|---|---|
| A timestamp or session id in the system prompt | Put volatile values in the *first user message*, not the prefix |
| Reordering the tool list between calls | Fix the order; sort once, deterministically |
| Adding or removing tools mid-conversation | Append new schemas; never reorder or remove |
| Rewriting earlier messages to "clean up" history | Append a correction instead |
| Selecting different few-shot examples per request | Fix a set per task type, byte-for-byte stable |
| Re-injecting a discovered tool schema every turn | It stays at its original position; later messages go after it |

**Tell**: `datetime.now()` or `uuid4()` in a system prompt template; a tool list
built from `set` or `dict` iteration order; any history rewriting.

## Prompt engineering: organisation beats wording

The measured finding, from a controlled ablation on Tau-Bench: keeping all the
same rule content but **removing the hierarchy and converting an ordered process
into an unstructured list of rules dropped task success by over 30%.** Changing
tone and style barely moved it.

The mechanism is visible in the failure. Once "verify identity before processing
a refund" was split from its process, the agent sometimes refunded without
verifying. In a flat list, the model cannot see priority or dependency.

**So write an SOP, not a rulebook.**

```
Step 1: Validate
   file exists and is readable
   - if not → log and stop
   ↓
Step 2: Classify
   determine type from extension and content
   ↓
Step 3: Preprocess
   config file → back it up first
   over 1MB    → stream it
   ↓
Step 4: Execute
```

A process lets the model answer "which stage am I in, and what happens next?".
A rule list makes it search.

**The litmus test**: an LLM is a highly capable new colleague who has never seen
your conventions. If they would read your prompt and still not know what to do
first, neither will the model.

### The rest of the toolkit

- **Structure carries meaning.** `<working_directory>/app/src</working_directory>`
  tells the model what it is looking at; `Current dir: /app/src` makes it infer
  the relationship across a colon. XML for machine-precise semantics, Markdown
  for hierarchy. Use both.
- **Salience is a budget.** `NEVER do X` lands harder than "please avoid X" — and
  overuse spends the effect. Reserve caps for the two or three rules that must
  not be missed. A document where everything is bold has no emphasis.
- **Vague rules produce unstable behaviour.** "Choose the appropriate billing type
  based on the situation" classifies identical inputs differently on different
  runs. Make it executable: "NEVER use percentage_based for refunds and
  cancellations; use fixed_fee." Numbers, thresholds, explicit exclusions.
- **Few-shot where rules fail.** When the wanted output resists description —
  tone, a report shape, the difference between a useful objection and a
  contrarian one — two or three examples beat a page of abstraction. Cover
  boundary cases; ten near-duplicates dilute attention and cost tokens.
- **Do not concatenate messages by hand.** Use the role system. Merging tool
  results into user messages destroys the model's trained basis for telling
  instructions from data, which is also a security control (below).

## Tool design

A tool description is a prompt, and it is the only thing the model has when
deciding whether to call it.

- **Say what it does *and when to use it*.** A description reading like an API doc
  produces a tool called at the wrong moment, or never.
- **State boundaries explicitly.** "NEVER invoke grep as a Bash command — use the
  Grep tool" is the kind of line that changes behaviour.
- **Put concrete examples in the schema.** `timezone: 'America/New_York'` beats
  `timezone: string`.
- **Name relationships between tools.** "Use Read at least once before Edit."
- **Narrow parameters beat free-form strings.** An enum cannot be hallucinated; a
  string can. Every constraint expressible in the schema is one the model cannot
  get wrong.
- **Error messages are for the model.** "Invalid input" is a dead end. "Expected
  an ISO 8601 date, got '3 days ago'" gets fixed on the retry.

Removing descriptive text while keeping signatures raised tool-call error rates
by **45%** in the same ablation. The description is not documentation; it is the
interface.

**Before adding a tool**, check whether an existing one covers it with one more
parameter. A near-duplicate makes the selection problem harder for *every* call,
not just this one — the cost is paid across the whole tool list.

## Progressive disclosure

The pattern behind Skills, tool search, and every well-behaved reference system:

```
L0  name + one-line description       always in the prefix, ~100 tokens
L1  the full instructions              loaded when the model decides it needs them
L2  linked files, scripts, examples     read only if L1 says to
```

Two properties make it work: the always-on cost stays tiny, and loaded content
**appends at the end**, which is cache-safe.

The common mistake is building L1 and skipping L0 discipline — a "skill" whose
description runs three paragraphs is back to pinning it.

## Memory that does not rot

The measured failure of append-only memory: a system told a rule, then told a
replacement, goes on citing the retired rule. The arm that *replaced* entries
recovered; the arm that only appended did not.

- **Entries need stable ids and a status**, so a superseded one can leave the
  working set without leaving the record.
- **Merge deterministically, in code.** Never have a model rewrite the whole
  memory file: successive attempts at brevity gradually erase the rare details
  that were the reason to keep it.
- **Retire, do not delete.** A superseded entry naming its successor is an audit
  trail; a deleted one is a mystery.
- **Read an index, not the file.** One line per entry, then open the two that
  matter. Otherwise the whole file is re-read every session.

## When multi-agent actually helps

Default to assuming it does not. A serial chain can only lose information
relative to its input — passing a conclusion down a pipeline discards the
evidence behind it.

    the second agent has evidence the first could not have
      (fresh context on a large codebase, a different tool, a different modality)
      → real gain
    the second agent re-reads the same text and comments
      → no gain, and measurably harmful: GPT-4 asked to self-correct with no
        external feedback LOWERED its accuracy
    independent samples, aggregated at the end
      → the one pattern the critique exempts. Draw them in parallel, never
        in sequence

**Give the strongest model to the planner.** A weak planner is unrecoverable —
everything downstream executes its conclusion faithfully. Cheap models belong
where a miss is caught by the next stage.

**A judge from the same model family shares the blind spot** that produced the
disagreement it is judging.

## Non-determinism is a design constraint

The same prompt does not produce the same output twice.

- **Constrain the shape; do not parse prose.** A validated schema at the
  boundary, not a regex over natural language.
- **Decide what happens on a malformed response before shipping.** It will
  happen. Retrying with the validation error included is usually right; crashing
  is acceptable; silently taking the wrong branch is not.
- **Every loop needs its cost bounded — and you have to check the bound actually
  binds.** Agents do get stuck repeating one tool call, and something has to end
  that. A turn cap is the obvious instrument, and the trap is assuming it works
  because you configured it: measured in this plugin, agents ran 55, 70 and 84
  turns against caps of 25 and 30 and finished normally. The cap was declared,
  inert, and believed in for a fortnight. Assert the bound by observation — run
  something past it on purpose and see what comes back — before you rely on it.
- **A bound whose overrun is invisible is worse than none.** Decide what the
  caller receives when a run is cut short. A loop that returns its *opening* line
  instead of its findings has spent the full price and delivered nothing, and it
  reads downstream as a clean result rather than a failure. Whatever consumes the
  output needs a way to tell "finished with nothing to say" from "stopped before
  it said anything".
- **One run proves nothing** at temperature above zero.

## Prompt injection

Every perception tool is an entry point: web pages, documents, PDFs, image
metadata, retrieved knowledge, and third-party skills — the sharpest case,
because a skill enters the context *as instructions*.

Tools make injection worse than for a chatbot. The worst case for a chatbot is
bad text; here it is a deleted file or an exfiltrated secret.

- **Tag the source.** `<external_content source="webpage">…</external_content>`
  marks it as material, not instruction.
- **Use the role system properly** so the model's trained priorities apply.
- **Sanitising input is auxiliary.** Wording variations defeat it.
- **Context-level defence is the first layer, not the only one.** Permissions,
  sandboxing and confirmation on irreversible actions are what actually hold.

**Review a third-party skill like code you are about to execute.** That is closer
to what it is than documentation.

## Status and state belong in code

A status line maintained by code beat one maintained by a frontier model
summarising its own history — and the model-maintained version scored *below
having no status line at all*.

The generalisation: **anything countable should be counted, not estimated.** File
counts, elapsed turns, budget remaining, which step you are on. A model asked to
track it will drift, and the drift is invisible.

The other half is equally measured: raw readings with no rule attached moved
behaviour by 2–3 points; readings **plus an operational rule** moved it by 19–49.
Never emit a number without saying what to do at what threshold.

## Evals, or it is guesswork

- A prompt change that "seems better" is unmeasured. Keep a small set of real
  cases with known outcomes, and a baseline arm without the change.
- **A handful of real cases beats a large synthetic set.**
- **Verify each case can fail.** A case that passes on the unmodified fixture is
  not a test.
- **Regression testing needs Pass^k** (all k succeed), not Pass@k (any one
  succeeds). At a 60% single-attempt rate, Pass@5 ≈ 99% and Pass^5 ≈ 8% — same
  system, 91-point gap, decided by the metric.

## Cheap models fail at underspecification, not difficulty

A smaller model with a precise contract and a check it can run will often match a
larger one given a vague instruction.

**Before reaching for a bigger model, ask whether the task is underspecified.**
That is usually cheaper to fix, and fixing it helps the bigger model too.

The routing corollary: push work toward *clear goal + automated verification*
rather than adding ceremony evenly. Tasks in that quadrant run well on cheap
models; tasks outside it run badly on everything.

## Review checklist

1. Does anything volatile sit in the cached prefix?
2. Is the tool list order stable across calls?
3. Is the system prompt a process, or a flat pile of rules?
4. Does every tool description say *when* to use it, not just what it does?
5. Are there two tools that overlap?
6. Do error messages tell the model what to do differently?
7. Is every loop's cost bounded, and does the bound leave the work readable when it fires?
8. Is external content tagged as data before it enters the context?
9. Is anything countable being estimated by the model instead of counted?
10. Does any number reach the model without a rule for acting on it?
11. Does the memory retire superseded entries, or only append?
12. Is a second agent adding evidence, or re-reading the same text?
