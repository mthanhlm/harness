---
name: implement
description: Build what an approved contract describes, on a cheaper model. Use after /harness:contract has been agreed, or when a task is already specified precisely enough that the work is mechanical rather than a judgement call.
argument-hint: "[optional note, e.g. 'start with the failing test']"
model: sonnet
effort: high
allowed-tools: Read, Write, Edit, Grep, Glob, Bash, TodoWrite
---

# Execute the contract

Note: **$ARGUMENTS**

This skill runs on Sonnet, deliberately. The expensive judgement — is this worth
building, where does it go, what already exists, what proves it works — has
already happened and is written down. What remains is execution against a spec,
which is the part a cheaper model does well.

The bet is specific: **cheap models fail on underspecified work, not on hard
work.** A contract removes the underspecification, and the harness's checks catch
the mistakes. If both hold, this costs a fraction of doing it on Opus.

## 1. Read the contract first

```bash
cat "${CLAUDE_PLUGIN_DATA}/contracts/${CLAUDE_SESSION_ID}.md"
```

If there is no contract, or its `status:` is not `approved`, **stop and say so.**
Do not start building. Without a spec this skill is just a cheaper model guessing,
which is the thing it exists to avoid. Suggest `/harness:contract` instead.

The contract is now your instruction set. It tells you the files in scope, what is
explicitly out of scope, what to reuse, the expected size, and the command that
proves the work is done.

## 2. Write the failing test first

The contract names it. Write it, run it, and confirm it fails for the reason you
expect. A test written after the implementation tends to assert what the code
does rather than what it should do — and a test you never watched fail is a test
you have no evidence works.

## 3. Build only what is in scope

Work through the contract's file list. For each one:

- **Reuse what the contract says to reuse.** It was chosen after a search; do not
  re-litigate it by writing a fresh helper because that felt faster.
- **Follow the conventions of the file you are in.** Match the surrounding
  naming, error handling and structure. A correct change in a foreign style still
  costs the next reader.
- **Stay inside the fence.** If the work genuinely needs a file the contract does
  not list, that is new information, not a licence to widen: say so, and ask
  whether to amend the contract. The end-of-turn gate checks this, so quietly
  going outside scope will be caught anyway — better to raise it than be caught.

Do not add error handling for cases that cannot occur, options nobody asked for,
or abstractions with one caller. The contract's line estimate is the signal: if
you are running at double it, stop and say why before continuing.

## 4. Let the checks do their job

After each edit the harness checks the file you touched, and blocks if the edit
introduced a problem. When that happens, fix exactly what it reports. It has
already confirmed the problem is new — other diagnostics in the same file existed
before you arrived and are not yours to fix.

At the end of the turn it runs the project's tests and build. Then run the
contract's verification command yourself and paste the result. "It should work"
is not a result.

## 5. Know when to hand back

Escalate rather than grind. Say plainly that you are handing back, and why, when:

- The contract turns out to be wrong — the approach does not survive contact with
  the code.
- The same check fails three times and your fixes are not converging.
- The work needs a design decision the contract does not settle.

Handing back after two minutes is cheap. Twenty turns of a cheap model failing to
converge is not cheaper than having asked, and it is exactly the token waste this
plugin was built to remove. The next turn returns to your session's normal model,
so `/harness:contract` or a plain question picks up where you left off.

## 6. Report

State what changed, the verification command you ran and its output, and anything
you noticed but deliberately left alone because it was out of scope. That last
list is useful — it is the backlog the contract kept you from wandering into.
