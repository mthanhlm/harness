---
name: verify-tests
description: Prove whether the tests would actually catch a regression, by breaking the implementation on purpose and checking they go red. Use when tests pass on the first run, before trusting a suite you did not write, or when asked whether the tests are any good.
argument-hint: "[file or module to check]"
model: opus
effort: xhigh
allowed-tools: Bash, Read, Grep, Glob, Edit, Task
---

# Are these tests real?

Target: **$ARGUMENTS** (the tests covering the current change if empty)

A passing suite proves the tests pass. It does not prove they would fail if the
code were wrong, and those are completely different claims. A suite of tests that
cannot fail is worse than no suite: it costs time to run and it grants confidence
about code nobody has actually checked.

Reading tests can only ever suggest this. Mutation proves it.

## 1. Get to a known-good state

```bash
git status --short
```

**Everything must be committed or stashed before you start.** This procedure
deliberately breaks source files, and a dirty tree makes the damage
indistinguishable from the user's work in progress. If the tree is dirty, stop
and say so rather than proceeding.

Then confirm the suite is green to begin with. Mutating against an already-red
suite tells you nothing.

## 2. Read the tests first

Use the `reviewer-tests` agent on the target. It will flag the ones that look
decorative: assertions on constants, mocks that supply their own answer, call
counts standing in for behaviour, expected values computed the same way the
implementation computes them.

That gives you a prediction. The mutations test it.

## 3. Mutate, one at a time

For each meaningful behaviour in the target, make one small wrong change, run
only the tests that cover it, and record whether anything went red. Then **revert
immediately**, before the next mutation. Never hold two at once — an unrevertable
mess is a real cost and the results stop meaning anything.

Mutations worth making, in rough order of value:

- **Invert a condition.** `if x` to `if not x`, `<=` to `<`.
- **Change a returned value.** Return a constant, an empty list, `None`.
- **Skip a side effect.** Comment out the write, the save, the emit.
- **Break a boundary.** Off-by-one on a slice, a limit, an index.
- **Remove a guard.** Delete a validation or permission check — if no test
  notices, the security of that path rests on nothing.

Keep the change small enough that a good test would obviously catch it. Deleting
a whole function proves little; every test breaks and you learn nothing about
which one was watching.

## 4. Report survivors

A **survivor** is a mutation that no test caught. Each one is a specific,
demonstrated hole:

> Returning `[]` from `get_active_users()` instead of the query result — full
> suite still passes. Nothing tests that it returns actual users.

That sentence is worth more than any amount of coverage percentage, because it
names the regression that would reach production unnoticed.

For each survivor: the mutation, which tests should have caught it, and the
assertion that would.

## 5. Restore and confirm

```bash
git diff
```

Must be empty, or exactly the user's original work. **Confirm this explicitly in
your report.** Leaving a mutation behind would be the single worst outcome of
this procedure — a deliberately broken line, in code the user believes was only
being inspected.

Then re-run the full suite and confirm it is green again.

## Rules

Do not mutate everything. Target the behaviour that matters: the logic a user
depends on, the checks that protect data, the paths that handle money or
permissions. Generated code, thin delegation and configuration are not worth the
runtime.

Do not conclude that untested code must be tested. Some genuinely should not be.
Report the hole, say what a regression there would cost, and let that decide.
