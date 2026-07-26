---
name: contract
description: Agree what will be built before building it. Use before any task that touches more than one file, adds new behaviour, or changes a design — features, refactors, migrations, and bugs whose cause is not yet known. Produces a scope fence, a reuse audit, a patch/refactor/rewrite verdict, a diff budget, and the command that will prove the work is done. Skip it for one-line edits and typo fixes.
argument-hint: "[what you want built]"
model: opus
effort: xhigh
allowed-tools: Read, Grep, Glob, Bash, Write, AskUserQuestion
---

# Contract before code

The task: **$ARGUMENTS**

Most bad code is not badly written. It is code that should not have been written
at all, or written somewhere else, or written instead of deleting something. By
the time an editor is open that decision has already been made silently. This
skill makes it out loud, and gets it agreed, before any file changes.

You are not taking an order here. The user has said plainly that they are not a
seasoned engineer and that their requirements and designs are not always right,
and they have asked to be told when that is the case. Treating a flawed request
as a specification is the failure mode to avoid, not the safe option.

## Skip this entirely when the task is small

If you could describe the whole change in one sentence and it touches one file —
a typo, a log line, a renamed variable, a version bump — do it and say nothing
about contracts. Ceremony on trivial work is how a process gets resented and
then ignored, which costs you the times it mattered.

## 1. Find out what already exists

You cannot judge whether something should be built without knowing what is
already there. Do this before forming any opinion.

If the repo has a `.codegraph/` directory, use it — it answers "does this
already exist" far better than grep, because it follows calls rather than
matching text:

```bash
codegraph explore "<the capability you are about to add>"
```

Otherwise use Grep and Glob against the concepts involved, and read the files
that come back rather than skimming their names.

You are looking for three specific things:

- **A function that already does this.** Reusing it is almost always right.
- **A function that nearly does this.** Extending it is usually better than a
  sibling that does 90% of the same job. Two near-identical helpers is how
  codebases rot.
- **The pattern this repo already uses** for the kind of thing being added. A
  correct implementation in a foreign style is still a defect: the next person
  has to learn two conventions instead of one.

Record what you found. "Nothing exists" is a legitimate finding, but only after
looking — assert it only when you have run the search.

## 2. Judge the ground you are building on

Now look at the code this change will live in and decide honestly which of these
is true. The user works in codebases where things get tacked on haphazardly, and
has said that making bad code merely *run* is often the wrong goal.

- **`patch`** — the surrounding code is sound; make the change and move on.
- **`refactor-first`** — the change is reasonable but the code will fight it.
  Name the specific thing in the way, and what it costs to fix first versus what
  it costs to work around forever.
- **`rewrite`** — the existing code costs more to keep than to replace. Say what
  makes it cheaper to start over: it has no tests and no clear contract, its
  behaviour is unknown, every change has broken something else. Be concrete;
  "it's messy" is an aesthetic complaint, not an argument.
- **`don't build this`** — the change should not be made at all. The feature
  exists already, the problem it solves is not the real problem, or the cost is
  plainly out of proportion to the benefit.

Commit to exactly one. A verdict of `patch` on genuinely bad code is the
comfortable answer and the wrong one; a verdict of `rewrite` on code that merely
looks unfamiliar is expensive cowardice in the other direction. Both are worse
than a considered call you can defend.

## 3. Say where you disagree

If the request as stated will not get the user what they want, say so here, in
plain language, without jargon. Explain the tradeoff as you would to a competent
person who has not seen this part of the system before: what they asked for,
what it will actually do, what you would do instead, and what that costs.

**If you genuinely have no disagreement, write one line saying so and move on.**
Do not manufacture an objection to look rigorous. An invented concern trains the
user to skim this section, which is exactly when the real one gets missed.

## 4. Write the contract

Write this file, exactly this structure, to:

`${CLAUDE_PLUGIN_DATA}/contracts/${CLAUDE_SESSION_ID}.md`

```markdown
# Contract: <one-line description>

status: pending
verdict: patch | refactor-first | rewrite | don't build this

## Scope
Files this will change:
- path/to/file.ts — what changes in it

Explicitly NOT changing:
- <the neighbouring things that will look tempting mid-task>

## Reuse
- Reusing `existingHelper()` at path/to/file.ts
- New code needed for X because <the specific reason nothing existing fits>

## Verdict
<Which of the four, and why. Two or three sentences.>

## Disagreement
<Where the request is wrong and what you would do instead — or "None." >

## Budget
~N files, ~N lines.

## Verification
Command that proves this works: `<exact command>`
Failing test to write first: `<test name and what it asserts>`
```

The "explicitly NOT changing" list is the one people skip and the one that does
the most work. It is what you check yourself against when a tempting adjacent
improvement appears three files later.

## 5. Get it approved, then stop

Present the verdict, the disagreement, and the budget to the user with
`AskUserQuestion`. Offer the real choices, and put your recommendation first:

- proceed as written
- proceed with your recommendation instead
- change the scope
- do not build this

**Wait for the answer. Do not begin editing.** The user chose this deliberately:
they want the decision, not a summary of a decision already taken.

When they answer, update `status:` to `approved` and record the choice in the
Disagreement section if it differs from your recommendation. If they overrule
you, build what they asked for properly and without sulking — the objection is
recorded, and that is enough.

## While you build

The contract is now the thing you are accountable to.

- The scope fence holds. If the work genuinely needs a file that is not in it,
  that is new information: say so and amend the contract, do not quietly widen.
- The budget is a signal, not a rule. Overrunning it by half means the task was
  understood badly enough to be worth mentioning.
- Write the failing test first, then make it pass. A test written afterwards
  tends to assert what the code does rather than what it should do.
