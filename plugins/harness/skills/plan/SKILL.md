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

Judgement is delegated to subagents, and the reason holds whatever model you are
running on: **each one reads in a context of its own and returns a conclusion.**
Your context is already full of this conversation, which is exactly what makes it
a poor place to search a codebase or read a diff cold. Theirs is empty and spent
entirely on the question.

Some of them also run on a stronger model than yours, and some deliberately run
on a cheaper one — searching and comparing do not need what simulating an
execution needs. Either way, **use them rather than substituting your own
opinion**:

| Subagent | For |
|---|---|
| `harness:reuse-auditor` | Does this already exist? Run it before planning any new code. |
| `harness:architect` | Is this code worth building on — patch, refactor-first, rewrite, or don't build? |
| `harness:reviewer-*` | The review at the end. |
| `harness:refuter` | Kills weak findings before they reach the user. |

**The `harness:` prefix is required.** These agents ship inside a plugin, so the
Task tool addresses them by a scoped name. `architect` alone fails with an
unknown-agent error.

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

Check whether this repo's own checks are actually running:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trust.py" status
```

If commands are withheld, the plan's **Verification** section is about to promise
something the end-of-turn gate cannot deliver — `npm test` named as the proof
will not run. Say so while planning, not afterwards, and either recommend
`/harness:trust` or pick a verification command that does not depend on the
withheld ones.

**Do not approve them on the user's behalf.** Running a stranger's commands is
their decision, and a plan skill quietly granting it would defeat the point of
asking. Noticing is your job; approving is theirs.

Read what this project already decided, before deciding anything:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/roadmap.py" show
```

Every session otherwise starts from nothing — the same ground gets re-covered
and the same deferred item gets deferred again. If the roadmap names something
this request touches, say so out loud rather than silently re-litigating it. And
if it contradicts what is being asked for now, that is worth raising: a past
decision is evidence, not an instruction, but reversing one by accident is how a
codebase acquires two of everything.

It builds the index if it is missing, does nothing if it is already there, and
says so plainly if CodeGraph is unavailable. Relay its line to the user when it
had to build one. Then search:

```bash
codegraph explore "<the capability in question>"
```

If it reported that CodeGraph is unavailable, fall back to Grep and Glob against
the vocabulary the codebase would plausibly use.

**Interview them, until nothing material is unresolved.** A requirement is
written by whoever has the problem, not by whoever knows the system, so it is
normally incomplete — and the person you are working with has said outright that
they cannot tell what they left out. Waiting for them to notice is not a plan.

**Read the code first.** This is a precondition, not a preference: it is what
stops the first round being twelve questions the codebase already answers.

Then ask in rounds, with these four bounds:

- **A question qualifies only if two answers would change the Scope file list,
  the Data flow, or the User flow.** Anything smaller, decide yourself and write
  it down. This is the whole test — "it would be good to know" is not one.
- **Up to four questions per round**, since `AskUserQuestion` takes four. One at
  a time is what turns an interview into an interrogation.
- **Every round offers "use your judgement for the rest"**, which ends the
  interview immediately and moves everything outstanding to *What you did not
  say*. They must always be able to stop without knowing anything.
- **Stop at three rounds**, or sooner when a round changes nothing in the draft.
  A fourth round means you are asking the code's questions, not theirs.

Two failure modes to avoid, in both directions. Asking a non-engineer something
they cannot have an opinion on — "write-through or write-behind?" — is worse than
deciding it yourself, because an arbitrary answer launders a guess into a
requirement and removes it from the list they could have reviewed. And if a round
returns nothing at all (a headless run has nobody to answer), treat every open
question as answered by your recommendation, record them all, and proceed.

The interview closes before the plan is presented. Stage 3 is approval, not
another round.

**But write down every gap, whether or not you ask about it.** The person you are
working with has said they do not always know what a requirement is missing, and
that is the normal case rather than a failing — a requirement is written by
whoever has the problem, not by whoever knows the system. So as you read, keep a
list of what the request did not say: the case it does not cover, the existing
behaviour it would change without mentioning it, the thing it implies but never
states. Each one goes in the plan's **What you did not say** section with the
assumption you made and what changes if that assumption is wrong.

Not asking is fine. Deciding silently is not — a decision the user never saw is
one they cannot correct, and they only find out when the built thing is wrong.

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
<**This list is parsed by the end-of-turn gate, and anything it cannot read is
not protected.** One literal repo-relative path per bullet, with its extension,
as the first thing after the dash. No globs (`agents/*.md`), no braces
(`skills/{a,b}/SKILL.md`), no two paths on one line — each of those silently
parses to nothing, and a file left out of the fence can be rewritten with the
gate still reporting clean. If that makes the list long, the list is long.>

Also name the failing test's own file here. The build is told to write it first,
and the fence will otherwise flag it as an unagreed change.

Slices (only when three or more files can be built at once):
- worker 1 — path/to/a.ts, path/to/b.ts
- worker 2 — path/to/c.ts
<Every file appears under exactly one worker. Two workers sharing a file lose
code silently, with no error and no conflict marker. If the work cannot be cut
that way, say so and leave this out — serial is the correct answer more often
than not.>

Explicitly NOT changing:
- <the neighbouring things that will look tempting mid-task>

## Reuse
- Reusing `existingHelper()` at path/to/file.ts
- New code needed for X because <the specific reason nothing existing fits>

## Verdict
<Which of the four, and why — from the architect subagent. Two or three sentences.>

## What you did not say
<Every gap in the requirement, with the assumption made and what changes if it
is wrong. One line each. This is not a list of your doubts — it is the list of
decisions the user never got to make. Write "Nothing material." only when that
is true after reading the code, which is rarer than it feels.>

- <the gap> → assumed <X>; if it should be <Y> instead, <what changes>

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

## Stage 2b — Have the plan argued against

Once the plan is written and **before** presenting it, if it spans more than
about three files, launch `harness:plan-challenger` on it. Below that, skip —
ceremony on a small request is how a process gets bypassed.

It reads the plan, not the code, and it is briefed to argue for less: what to
cut, what the smaller version is, and whether the verification would actually
fail on a broken implementation. Give it the contract's path and the request.

This is the one place nothing else covers. Everything downstream checks whether
the code matches the plan; **nothing else asks whether the plan was right**, and
by then it is the most expensive thing to have got wrong.

Act on it before presenting. If you cut something, cut it. If you disagree, keep
it and say why in one line — the user should see that the argument happened and
how it was settled, not a plan that quietly survived it.

## Stage 3 — Get approval, and actually stop

Present the goal, the user flow, the data flow, the verdict and the budget —
and **read out "What you did not say"**. That section is the one the user cannot
supply themselves, because it is a list of things they did not know to mention.
Two lines of it are worth more than a paragraph restating what they asked for.

Then `AskUserQuestion` with real choices, your recommendation first:

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
parallel — each in a context spent only on the diff — and refutes each finding
before reporting.

Do not skip this because the work looks fine. Looking fine is what a defect does.

## Stage 6 — Report, and leave a record

In order: what was built, the verification command and its real output, what the
review found, and anything you deliberately left alone because it was out of
scope. That last list is the backlog the scope fence protected you from wandering
into.

If the review found nothing, say so plainly. Sound work reviewing clean is a
result.

Then write down what the next session would otherwise have to rediscover:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/roadmap.py" append "<one-line title>" <<'EOF'
- decided: <a choice made here, and why, in one line>
- deferred: <something real that was found and not fixed, and why>
EOF
```

**Decisions and deferred work only.** Not what happened — a session narrative
rots into a wall nobody reads, and then the roadmap is worth nothing to the
session that needed it. The test is whether it would change what someone does
next. "Rewrote the parser" would not; "chose to withhold repo-authored commands
rather than allowlist them, because an allowlist has to contain npm" would.

Nothing worth recording is a legitimate answer on a small change. Say so and
skip it rather than padding the file.

## When to stop and hand back

Say so plainly rather than grinding, when:

- the plan turns out to be wrong once you are inside the code
- the same check fails three times and your fixes are not converging
- the work needs a design decision the plan does not settle

Stopping after two minutes is cheap. Twenty turns of not converging is not, and it
is the exact waste this plugin exists to remove.
