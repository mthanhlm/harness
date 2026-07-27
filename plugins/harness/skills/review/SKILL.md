---
name: review
description: Review the current changes with the specialists the change actually needs, refuting each finding before reporting it. Use after implementing something and before treating it as done, or when asked to review a diff, a branch or recent work.
argument-hint: "[optional focus, e.g. 'security' or a file path]"
effort: xhigh
allowed-tools: Bash, Read, Grep, Glob, Task
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
cat "${CLAUDE_PLUGIN_DATA}/contracts/${CLAUDE_SESSION_ID}.md" 2>/dev/null
```

A contract turns review from an open question into a specific one. Check the
diff against it:

- Did everything it promised get built?
- Did anything outside the scope fence change?
- Was the verification command actually run, and did it pass?

A gap against an agreed contract outranks anything a reviewer finds on taste.

## 3. Pick the crew

Use the `crew` skill to select the roles this change needs, and tell the user
which ones you chose and which you skipped. Running every reviewer on every
change produces noise, and noise trains people to skim.

## 4. Run the reviewers in parallel

Launch them in a **single message** so they run concurrently, and **wait for them
— pass `run_in_background: false`.** Agents background themselves by default, and
there is no review to report without their findings. Give each the same
brief: the diff range, the contract if one exists, and the focus argument if the
user gave one.

## 5. Refute every finding before it reaches the user

Send each finding to the `refuter` agent, in parallel. Only findings that survive
get reported.

This step is not optional and it is not ceremony. A reviewer told to find
problems will report some on flawless code, because that is the task it was
given. Acting on an invented finding adds a null check for a case that cannot
occur, or an abstraction for a problem that does not exist — the exact
over-building this harness exists to prevent. The refuter defaults to refuted
when unsure, and that asymmetry is deliberate.

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
