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

The always-on roles run. For each conditional one, judge it against what
actually changed rather than running everything:

- `reviewer-security` — only if the change touches input, auth, queries, paths,
  external calls or config.
- `reviewer-perf` — only if it loops over user data, queries, renders lists, or
  sits on a request path.
- `reviewer-tests` — if tests changed, or if behaviour changed without them.
- `reviewer-docs` — if a signature, name, default, command or public behaviour
  changed.

Running every role on every change is how a review becomes noise, and noise
trains people to skim. A role with nothing to look at will produce something
anyway, because that is what it was asked to do.

Whether the design was right at all is not a review question. It was settled
before the code existed, by `harness:challenger` at the top of `harness:plan`.
Asked here, it can only recommend rewriting work that is already done.

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

If everything was refuted, say exactly that. Sound work reviewing clean is a
result, and manufacturing a finding to look diligent wastes a turn and costs
trust.

## 7. Offer, do not act

Ask before fixing anything. The user asked for a review, and a review that
silently rewrites the code is not a review. Rank the findings, recommend which
are worth acting on now, and say plainly which you would leave.
