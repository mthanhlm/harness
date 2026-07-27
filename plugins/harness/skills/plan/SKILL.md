---
name: plan
description: The way to start any real piece of work. Works out what you actually want, checks what already exists, judges whether the code is worth building on, gets your approval, then builds and reviews it. Use for features, refactors, migrations, and bugs with unknown cause — anything beyond a one-line edit.
argument-hint: "[what you want]"
effort: xhigh
allowed-tools: Read, Grep, Glob, Bash, Write, Edit, AskUserQuestion, Task, Skill, TodoWrite
---

# Plan, build, review

The request: **$ARGUMENTS**

This is the single entry point. You take this all the way from a vague sentence to
reviewed, working code, stopping exactly once — to get the plan approved before
anything is edited.

The person you are working with has said plainly that they are not a seasoned
engineer, that their requirements and designs are not always right, and that they
want to be told when that is the case. Treating a flawed request as a
specification is the failure to avoid here, not the safe option.

## Where the expensive thinking happens

Your own reasoning runs on the session's model, which is the cheaper one. That is
deliberate: orchestration and editing are high-volume, low-judgement work.

The judgement is delegated to subagents that run on a stronger model regardless of
yours. **Use them — do not substitute your own opinion for theirs**, because
yours is the cheaper one:

| Subagent | For |
|---|---|
| `reuse-auditor` | Does this already exist? Run it before planning any new code. |
| `architect` | Is this code worth building on — patch, refactor-first, rewrite, or don't build? |
| `reviewer-*` | The review at the end. |
| `refuter` | Kills weak findings before they reach the user. |

Launch independent ones in a **single message** so they run concurrently, and
**wait for them — pass `run_in_background: false`.** Subagents run in the
background by default, and you cannot draft a plan without their findings. A
backgrounded agent here leaves you idling for a notification instead of working.

## Stage 1 — Understand what is actually wanted

Read the request again and work out what the person is trying to achieve, not
just what they typed. Then go and read the code — you cannot plan against a
codebase you have not looked at.

Before searching, make sure the index exists — it is built per repository, so a
fresh clone has none even with CodeGraph installed globally:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/codegraph_ready.py"
```

It builds the index if it is missing, does nothing if it is already there, and
says so plainly if CodeGraph is unavailable. Relay its line to the user when it
had to build one. Then search:

```bash
codegraph explore "<the capability in question>"
```

If it reported that CodeGraph is unavailable, fall back to Grep and Glob against
the vocabulary the codebase would plausibly use.

**Ask only when you genuinely cannot resolve something.** Read the code first;
most ambiguity dissolves once you have. Use `AskUserQuestion` when two readings
would lead to materially different work — a real fork, not a detail you could
reasonably decide and state. Keep it to one round, folded into the approval
question in Stage 3 where possible. Ceremony on a clear request is how a process
gets resented and then bypassed.

## Stage 2 — Draft the whole picture

Write this file, exactly this structure, to:

`${CLAUDE_PLUGIN_DATA}/contracts/${CLAUDE_SESSION_ID}.md`

```markdown
# Plan: <one line>

status: pending
verdict: patch | refactor-first | rewrite | don't build this

## Goal
<What the person is trying to achieve, in their terms, not yours. One paragraph.>

## User flow
<What a person actually does, step by step, once this exists. Where they start,
what they see, what they click or send, what comes back. If nothing a user
experiences changes, say so — internal work is allowed to say "none".>

## Data flow
<What moves where. What comes in, what it turns into, where it is stored, what
reads it later. Name the actual tables, endpoints, files and queues. This is
where wrong assumptions surface earliest.>

## Surfaces touched
<Which parts of the system: UI, API, database, background jobs, config,
third-party services. One line each.>

## Scope
Files this will change:
- path/to/file.ts — what changes in it

Explicitly NOT changing:
- <the neighbouring things that will look tempting mid-task>

## Reuse
- Reusing `existingHelper()` at path/to/file.ts
- New code needed for X because <the specific reason nothing existing fits>

## Verdict
<Which of the four, and why — from the architect subagent. Two or three sentences.>

## Disagreement
<Where the request is wrong and what you would do instead — or "None.">

## Risks
<What could go wrong, what is still unknown, what you had to assume.>

## Budget
~N files, ~N lines.

## Verification
Command that proves this works: `<exact command>`
Failing test to write first: `<test name and what it asserts>`
```

Two sections do the most work and are the ones people skip. **"Explicitly NOT
changing"** is what you check yourself against when a tempting adjacent
improvement appears three files later. **"Data flow"** is where a
misunderstanding becomes visible while it is still free to fix.

On **Disagreement**: if you genuinely have none, write "None." and move on. Do not
manufacture an objection to look rigorous — an invented concern trains the reader
to skim, which is exactly when the real one gets missed.

## Stage 3 — Get approval, and actually stop

Present the goal, the user flow, the data flow, the verdict and the budget. Then
`AskUserQuestion` with real choices, your recommendation first:

- proceed as planned
- proceed with your recommendation instead (when you disagreed)
- change the scope
- do not build this

**Wait. Do not edit anything before the answer.** This is the one interruption in
the whole flow, and it is the point of the flow.

When they answer, set `status: approved` and record their choice in the
Disagreement section if it differs from what you recommended. If they overrule
you, build what they asked for properly and without sulking — the objection is on
the record, and that is enough.

## Stage 4 — Build it

Invoke the `implement` skill and follow it. It holds the rules for building
against an approved plan: failing test first, stay inside the scope fence, reuse
what the plan said to reuse, fix what the automatic checks report.

The checks run on their own as you edit. A check that fires has already confirmed
the problem is new — other diagnostics in the same file predate you and are not
yours to fix.

## Stage 5 — Review it

When the build is done and the verification command passes, invoke the `review`
skill. It picks the specialists this particular change needs, runs them in
parallel on a stronger model, and refutes each finding before reporting.

Do not skip this because the work looks fine. Looking fine is what a defect does.

## Stage 6 — Report

In order: what was built, the verification command and its real output, what the
review found, and anything you deliberately left alone because it was out of
scope. That last list is the backlog the scope fence protected you from wandering
into.

If the review found nothing, say so plainly. Sound work reviewing clean is a
result.

## When to stop and hand back

Say so plainly rather than grinding, when:

- the plan turns out to be wrong once you are inside the code
- the same check fails three times and your fixes are not converging
- the work needs a design decision the plan does not settle

Stopping after two minutes is cheap. Twenty turns of not converging is not, and it
is the exact waste this plugin exists to remove.
