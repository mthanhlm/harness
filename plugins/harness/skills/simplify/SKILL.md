---
name: simplify
description: Remove code that is not earning its place — duplicated capability, speculative abstraction, unused options, dead paths, and comments that restate or contradict the code. Use after something works and before calling it done, or when a diff came out larger than the task warranted.
argument-hint: "[optional path to focus on]"
effort: xhigh
allowed-tools: Bash, Read, Grep, Glob, Edit, Task
user-invocable: false
---

# Simplify what was just built

Focus: **$ARGUMENTS** (the whole change if empty)

Code is written under pressure to make something work, and the shape that gets
there first is rarely the shape worth keeping. This pass runs once it works and
asks a single question of every added line: **is this earning its place?**

## 1. Start with the built-in pass

Claude Code ships a `simplify` skill that already covers reuse, simplification,
efficiency and altitude. Run it first rather than reimplementing it:

> Use the `simplify` skill on the current changes.

Wait for it to finish. What follows is the part it does not cover — the specific
complaints this harness was built around.

## 2. Duplicated capability

The most expensive thing in any diff, because it is not one mistake but a
permanent tax: two functions that nearly agree will drift, and then callers of
each get different behaviour from the same intent.

Use the `harness:reuse-auditor` agent against what was added — it ensures a CodeGraph
index exists first, then follows calls rather than text to find duplicates that
are named differently, which is how they got written twice in the first place.

Prefer extending the existing thing over keeping a near-twin. But do not force
it: a helper contorted to serve two callers, with three boolean parameters to
switch between them, is worse than two clear functions. Say so when that is the
honest answer.

## 3. Abstractions, defensive code and comments

`agents/reviewer-bloat.md` already states the criteria for one-caller
abstractions, defensive code against the impossible, and comments that restate
or go stale — read it rather than a second copy of the same rule here. The
difference is that this pass can act: where it finds one of those, inline the
abstraction, remove the impossible check (after confirming the case really
cannot occur — trace where the value comes from, do not assume), and fix or
delete the comment. `reviewer-bloat` only reports; you edit.

## 4. Documentation that is now wrong

Anything the change made false: docstrings with parameters that no longer exist,
READMEs describing an old command, examples that would fail if run, a stale
`CLAUDE.md` line — which misleads every future session, not just this one.

Use the `harness:reviewer-docs` agent if the change touched signatures, names, defaults,
commands or public behaviour.

Only fix what this change broke. Pre-existing gaps elsewhere are not this diff's
problem, and pulling them in is exactly the scope creep worth avoiding.

## 5. Verify, then report

Deletion is a change like any other. Run the project's checks afterwards and
confirm they still pass — a simplification that breaks a caller cost more than
the lines it saved.

Report what you removed and the line count. If you found nothing worth removing,
say that plainly: a tight diff that survives this pass is a good outcome, and
inventing a removal to look productive is its own kind of waste.
