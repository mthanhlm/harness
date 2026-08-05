---
name: review
description: Review the current changes with the specialists the change actually needs, refuting each finding before reporting it. Use after implementing something and before treating it as done, or when asked to review a diff, a branch or recent work.
argument-hint: "[optional focus, e.g. 'security' or a file path]"
effort: xhigh
allowed-tools: Bash, Read, Grep, Glob, Edit, Task, Skill, AskUserQuestion
---

# Review the change

Focus: **$ARGUMENTS** (review everything that changed if empty)

The reviewer that wrote the code is the worst reviewer of it. It knows what the
code was meant to do, so it reads intent instead of text, and the defect hides in
the gap between the two. Every role here runs in a fresh context that sees the
diff and nothing else.

## 1. Establish what changed

```bash
git diff HEAD --stat && git status --short
```

If there is nothing uncommitted, review the branch against its base instead. Say
which range you are reviewing before you start — a review of the wrong range is
worse than none, because it reports clean.

## 2. Read the contract, if there is one

```bash
CONTRACT=$(CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/contract.py" path) && cat "$CONTRACT"
```

If that prints nothing there is no contract, which is a normal thing to review
without. What is not normal is a contract that exists under a name nothing reads,
so if the command errors, say so rather than reviewing as though none was written.

A contract turns review from an open question into a specific one. Check the
diff against it:

- Did everything it promised get built?
- Did anything outside the scope fence change?
- Was the verification command actually run, and did it pass?

A gap against an agreed contract outranks anything a reviewer finds on taste.

## 3. Pick the reviewers

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/crew.py" after "$ARGUMENTS"
```

This returns the changed files, the domains **their paths** put beyond argument,
and the roles for this phase — split into the ones that always run and the ones
you decide on. **Launch by the `subagent_type` field, not `name`:** these agents
ship inside a plugin, so the Task tool addresses them as `harness:reviewer-perf`.
The bare name fails with an unknown-agent error that reads like you did not
bother to run it, so the report gives you both and you want the second.

**Every role the report marks `always: true` gets launched.** Not most of them,
and not the ones that look relevant — a role is marked always-on because the
judgement about when it applies was already made. In KD-547 `reviewer-bloat` was
simply not launched in the final round, nothing noticed, and the byte-comparison
it had run in the previous round was the check that would have caught a corrupted
file. If you skip one anyway, name it and the reason in the crew sentence below,
so the omission is something the user can see and overrule.

For each conditional one, judge it against what actually changed rather than
running everything:

- `reviewer-security` — only if the change touches input, auth, queries, paths,
  external calls or config.
- `reviewer-perf` — only if it loops over user data, queries, renders lists, or
  sits on a request path.
- `reviewer-tests` — if tests changed, or if behaviour changed without them.
- `reviewer-docs` — if a signature, name, default, command or public behaviour
  changed.
- `reviewer-coherence` — if the change adds a mechanism, a migration, a config
  field, a generated or exported file, or a second home for something that
  already exists.

Running every role on every change is how a review becomes noise, and noise
trains people to skim. A role with nothing to look at will produce something
anyway, because that is what it was asked to do.

**Whether the design was right at all is still not a review question.** It was
settled before the code existed, by `harness:challenger` at the top of
`harness:plan`, and asked here it can only recommend rewriting work that is
already done.

`reviewer-coherence` is not that question and its brief holds the line
explicitly. "This should not have been built" is out of bounds for it. "This is
now built in two places and they already disagree" is exactly what it is for, and
nothing else was looking: a change can be correct line by line and still leave a
guard waiting on a condition that cannot occur, or a file claiming to be a copy
of something it no longer matches. Those defects have no single line to point at,
which is why the line-by-line roles walk past them.

**Say the crew out loud before running it** — one or two lines, which roles and
why, which you skipped. This is where the user can say "you have missed the
database side of this", which is far cheaper now than after.

> Reviewing 6 files (schema.ts, route.ts, page.tsx). Roles: correctness, bloat,
> perf. Skipping security — nothing here takes user input.

Each reviewer loads its own domain knowledge from the paths it is given, so you
do not select that for them.

## 4. Run the reviewers in parallel

Launch them in a **single message** so they run concurrently, and **wait for them
— pass `run_in_background: false`.** Agents background themselves by default, and
there is no review to report without their findings. Give each the same
brief: the diff range, the contract if one exists, and the focus argument if the
user gave one.

## 4b. Check that every reviewer actually reported

**A reviewer's result can arrive without the report in it.** Not as an error —
as a short, plausible-looking string. What comes back is the agent's *opening*
line, followed by an `agentId` handle and a usage block:

    I'll start by reading the domain knowledge and the diff.
    agentId: a952bc936b5a207ce (use SendMessage with to: ...)
    <usage>subagent_tokens: 110506  tool_uses: 31  duration_ms: 220031</usage>

That is 256 bytes and it is not a report. The findings were written — in KD-547
four of eighteen launches came back this way, and the reports were recovered
afterwards from the agents' own transcripts, naming real defects in the code
under review. The tokens in that usage block were spent. Only the answer was
lost.

**Judge it on what it says, not on how long it is.** Length looks like the
obvious test and it does not work: a genuine "nothing to report" written to the
template in these briefs measures 350 to 400 bytes, and the dropped results
observed so far run from 47 to 256 — the two ranges very nearly touch, so any
threshold you pick condemns real reports or waves stubs through. What separates
them is not size. It is that a report answers the review question, and a dropped
result talks about the work instead of doing it — future tense, no file named, no
finding and no check. *"I'll start by reading the diff"*, or a note-to-self about
what to look at next.

So before you refute anything, read each result you got back:

    it answers the question — findings, or
    "nothing found" plus what was checked   → a report, however short. Carry on
    it says what the agent was about to do,
    or breaks off mid-reasoning, and names
    no finding and no check                 → it did not report. Re-launch that
                                              reviewer once, same brief
    it names a check or a finding and then
    breaks off — no closing list of what
    was checked, no "nothing found"         → a partial report. Re-launch it
                                              once, and until the second result
                                              arrives treat the role as having
                                              covered nothing
    the second result answers no better     → stop. That role **did not report**,
                                              and step 6 says exactly that —
                                              never as clean

**The middle row is the one that will catch you out.** Truncation does not
politely land on the opening sentence — it lands wherever the run stopped, which
is usually after the agent has named a file or two. A result that opens *"Checked
the three callers of `formatRange` — all pass the new signature. Now let me look
at the DST boundary"* has named a check and found nothing yet, and the first row
will happily take it as a clean report. It is not one: it is the same failure a
paragraph later. Every brief here ends a no-findings report with the list of what
it checked, so a report that stops mid-thought is missing its own ending.

Re-launch **once**, not repeatedly. A second loss is information about the run,
not something to keep paying for.

Two things this is not. It is not a judgement about quality — "No perf findings.
Checked: the two loops in the diff, both over a fixed status list" is a complete
answer, and re-running it wastes a full reviewer to be told the same thing twice.
And it is not licence to summarise a result you never received: **you may never
infer what a missing reviewer would have said.**

## 5. Refute every finding before it reaches the user

Send each finding to the `harness:refuter` agent, in parallel. The `harness:`
prefix is required — these agents ship inside a plugin and the bare name fails. Only findings that survive
get reported.

This step is not optional and it is not ceremony. A reviewer told to find
problems will report some on flawless code, because that is the task it was
given. Acting on an invented finding adds a null check for a case that cannot
occur, or an abstraction for a problem that does not exist — the exact
over-building this harness exists to prevent. The refuter defaults to refuted
when unsure, and that asymmetry is deliberate.

## 5b. Run any mutation the test reviewer specified

`reviewer-tests` reports weak tests as a named mutation — *"returning a constant
from `total()` leaves this green"* — and deliberately does not run it. It has no
`Edit` tool, and it runs alongside the other reviewers on a shared working tree,
where an edit of its own would surface as a phantom finding in theirs.

By this step the fan-out is finished and the tree is quiet, so you can settle it
with evidence instead of an argument:

1. `git stash list` and `git status` first — the tree must be clean apart from
   the change under review. If it is not, skip this and report the mutation as
   unverified.
2. Apply the named mutation with `Edit`, one at a time.
3. Run the test the reviewer named.
4. **Revert immediately**, with `Edit` back or `git checkout -- <file>`, before
   running the next one. Never leave a mutation in place across two runs.
5. Confirm the file is back: `git diff -- <file>` must show only the change under
   review.

    the test stays green  → the finding is confirmed with proof. Report the
                            mutation and the passing run
    the test goes red     → the finding is wrong. Drop it, and say the test was
                            checked and holds

Skip this whenever the tree is dirty, the mutation is not a single localised
edit, or the test suite takes long enough that the tree would sit mutated. An
unverified finding reported as unverified is fine; a mutation left behind is not.

## 6. Report

One list, most severe first. Silently-wrong data above crashes, crashes above
degraded behaviour, everything real above anything cosmetic.

For each: file and line, one sentence naming the defect, and the concrete
scenario that produces it.

Then, in one line, what came back clean. "Correctness, perf and security found
nothing" is real information — without it, a short report is indistinguishable
from a lazy one.

**A role may only appear in that line if you read its findings.** A reviewer that
did not report is not a reviewer that found nothing, and it is never folded into
the clean list — it gets its own sentence, by name:

> reviewer-correctness did not report, on two attempts. Nothing here covers
> correctness; run `/harness:review` again before treating this as reviewed.

Say it even when it makes the review look incomplete, because it *is* incomplete
and that is the one fact the user cannot recover on their own. The failure this
prevents is specific and it has happened: a dropped report, described as a clean
one, over a change that had three defects in it.

If everything was refuted, say exactly that. Sound work reviewing clean is a
result, and manufacturing a finding to look diligent wastes a turn and costs
trust.

## 7. Offer, do not act

Ask before fixing anything. The user asked for a review, and a review that
silently rewrites the code is not a review. Rank the findings, recommend which
are worth acting on now, and say plainly which you would leave.
