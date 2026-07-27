---
name: crew
description: Assemble the set of specialists a job actually needs, across every domain it touches, and run them. Use before designing non-trivial work and after implementing it — anything spanning more than one area, such as a feature that touches a schema, an endpoint and a component at once.
argument-hint: "[before|after] [what the job is]"
effort: xhigh
allowed-tools: Bash, Read, Grep, Glob, Task
user-invocable: false
---

# Assemble the crew

Job: **$ARGUMENTS**

One generalist is the wrong shape for most real tasks. A paginated endpoint
backed by a new query and rendered in a component is a database job, a backend
job and a frontend job at the same time, and reviewing it as any one of those
misses two thirds of what could be wrong.

So the work is split two ways at once. **Lenses** are domain knowledge, loaded
into whoever is doing the work. **Roles** are jobs — auditing reuse, judging the
design, hunting defects — each running in its own context so its reading does
not crowd yours. A role carries the lenses its job needs, which is how one
reviewer holds several domains at the same time.

## 1. Work out who is needed

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/crew.py" ${1:-after} "$ARGUMENTS"
```

This returns the changed files, the lenses their paths and the task description
match, and the roles for this phase — those that always apply and those that
apply conditionally.

## 2. Decide the conditional roles yourself

The always-on roles run. For each conditional one, the registry states when it
applies; check that against what actually changed. Use judgement rather than
running everything:

- `reviewer-security` — only if the change touches input, auth, queries, paths,
  external calls or config.
- `reviewer-perf` — only if it loops over user data, queries, renders lists, or
  sits on a request path.
- `reviewer-tests` — if tests changed, or if behaviour changed without them.
- `reviewer-docs` — if a signature, name, default, command or public behaviour
  changed.
- `architect` — if the code is unfamiliar or messy, or the design is in question.

Running every role on every change is how a review becomes noise. A role with
nothing to look at will produce something anyway, because that is what it was
asked to do.

## 3. Tell the user the crew before running it

State it plainly in one or two lines — which lenses, which roles, and why. This
is the point where they can say "you have missed the database side of this",
which is far cheaper now than after the review.

> Crew: **database** + **backend** + **frontend** lenses (schema.ts, route.ts,
> page.tsx). Roles: reuse-auditor, correctness, bloat, perf. Skipping security —
> nothing here takes user input.

## 4. Run them in parallel

Launch the selected roles as subagents **in a single message**, so they run
concurrently rather than one after another. Give each the same brief: what
changed, what the contract agreed, and where to look.

## 5. Refute before reporting

Every finding goes to the `refuter` agent before it reaches the user, and only
survivors are reported. This is not ceremony. A reviewer asked to find problems
will produce some whether or not they exist, and acting on an invented one adds
a defensive branch for a case that cannot happen — which is precisely the
over-building this harness exists to prevent.

Run the refutations in parallel too.

## 6. Report

One list, most severe first. For each: what is wrong, where, and the concrete
consequence. Say explicitly what was checked and came back clean — "correctness
and perf found nothing" is information, and without it a short report is
indistinguishable from a shallow one.

If everything was refuted, say so. Finding nothing on sound work is a real
result, not a failure to look hard enough.
