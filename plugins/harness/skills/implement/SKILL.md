---
name: implement
description: Build what an approved plan describes. Loaded by the plan skill once you have approved it; also usable when a task is already specified precisely enough that the work is mechanical rather than a judgement call.
argument-hint: "[optional note, e.g. 'start with the failing test']"
effort: high
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, TodoWrite, Task
user-invocable: false
---

# Execute the plan

Note: **$ARGUMENTS**

This is the high-volume, low-judgement half of the work. The expensive judgement
is already done and written down: whether this is worth building, where it goes,
what already exists, what proves it works.

The bet is specific: **cheap models fail on underspecified work, not on hard
work.** The plan removes the underspecification, and the harness's checks catch
the mistakes. So the building belongs on a cheap model — either your own session
if it is already cheap, or `worker` subagents pinned to Sonnet, which are cheap
whatever you are running on.

## 1. Read the plan first

```bash
cat "${CLAUDE_PLUGIN_DATA}/contracts/${CLAUDE_SESSION_ID}.md"
```

If there is no plan, or its `status:` is not `approved`, **stop and say so.** Do
not start building. Without a spec this is just guessing at scope, which is the
thing it exists to avoid. Suggest `/harness:plan` instead.

The plan is now your instruction set. It tells you the files in scope, what is
explicitly out of scope, what to reuse, the expected size, and the command that
proves the work is done.

## 2. Write the failing test first

The plan names it. Write it, run it, and confirm it fails for the reason you
expect. A test written after the implementation tends to assert what the code
does rather than what it should do — and a test you never watched fail is a test
you have no evidence works.

## 3. Split the work, or decide not to

The plan's Scope section has a **Slices** block, and the plan was told to fill it
in whenever the file list ran past two files — including to write "serial,
because X must land before Y" when that is the answer. So read what it decided
rather than re-deriving it: the partition was worked out with the whole design in
view, which is not where you are standing now.

Fan out when the plan named **two or more slices that share no file**, and each
is real work rather than a one-line edit. Below that a single thread is faster,
because launching workers costs more in coordination than it saves.

If the plan left the block off entirely, that is a plan written before this was
expected. Decide it yourself on the same rule, and say which you chose.

Before launching, check the split for overlap yourself. **Two workers editing one
file lose code silently** — no error, no conflict marker, whoever writes last
wins. The edit gate now refuses the second worker's write rather than trusting
the plan, so the failure surfaces as a stalled slice instead of lost code; that
is a backstop, not a substitute for a correct partition. If the plan's assignment
overlaps, say so and fix it before launching.

Launch every worker in a **single message** so they run at once, and **pass
`run_in_background: false`** — agents background themselves by default, which
would leave you waiting on notifications instead of collecting results.

Give each worker a brief it can act on without asking anything, because it cannot
ask:

- the goal, in one or two lines, so it can tell a good change from a literal one
- **the exact files it owns**, and that it must not edit any other
- what the plan said to reuse, by name and path
- the verification command, so it knows what "done" looks like

Then wait. Do not start editing files yourself while workers are running — the
plan assigned those files to them, and you are now a writer nobody accounted for.

When they return, read what each one says it did **not** finish. That list is the
real state of the work; a worker that hit something it could not resolve reports
it rather than failing loudly.

## 4. Build only what is in scope

Work through whatever you did not delegate — the whole file list if you decided
against fanning out. For each file:

- **Reuse what the plan says to reuse.** It was chosen after a search; do not
  re-litigate it by writing a fresh helper because that felt faster.
- **Edit with `Edit` or `Write`.** Shell edits are recorded now, but after the
  fact — they miss the per-edit check, so a mistake surfaces at the end of the
  turn instead of immediately.
- **Follow the conventions of the file you are in.** Match the surrounding
  naming, error handling and structure. A correct change in a foreign style still
  costs the next reader.
- **Stay inside the fence.** If the work genuinely needs a file the plan does
  not list, that is new information, not a licence to widen: say so, and ask
  whether to amend the plan. The end-of-turn gate checks this, so quietly
  going outside scope will be caught anyway — better to raise it than be caught.

Do not add error handling for cases that cannot occur, options nobody asked for,
or abstractions with one caller. The plan's line estimate is the signal: if
you are running at double it, stop and say why before continuing.

## 5. Let the checks do their job

After each edit the harness checks the file you touched, and blocks if the edit
introduced a problem. When that happens, fix exactly what it reports. It has
already confirmed the problem is new — other diagnostics in the same file existed
before you arrived and are not yours to fix.

At the end of the turn it runs the project's tests and build. Then run the
plan's verification command yourself and paste the result. "It should work"
is not a result.

## 6. Know when to hand back

Escalate rather than grind. Say plainly that you are handing back, and why, when:

- The plan turns out to be wrong — the approach does not survive contact with
  the code.
- The same check fails three times and your fixes are not converging.
- The work needs a design decision the plan does not settle.

Handing back after two minutes is cheap. Twenty turns of a cheap model failing to
converge is not cheaper than having asked, and it is exactly the token waste this
plugin was built to remove. The next turn returns to your session's normal model,
so `/harness:plan` or a plain question picks up where you left off.

## 7. Report

State what changed, the verification command you ran and its output, and anything
you noticed but deliberately left alone because it was out of scope. That last
list is useful — it is the backlog the plan kept you from wandering into.
