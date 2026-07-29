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

This returns the changed files, the lenses **their paths** put beyond argument,
the full **catalogue** of lenses, and the roles for this phase.

**Launch a role by its `subagent_type` field, not its `name`.** These agents ship
inside a plugin, so the Task tool addresses them as `harness:reviewer-perf`, not
`reviewer-perf`. The bare name fails with an unknown-agent error that reads like
you did not bother to run it, so the report gives you both and you want the
second.

## 2. Pick the task's lenses from the catalogue

`lenses_from_files` is settled — a path is a fact, and `db/schema.ts` is a
database file whoever is asking. Those load.

The catalogue is the rest, and choosing from it is your job rather than the
script's. It used to be done by keyword matching, which fired `ui` inside
"b**ui**ld" and `auth` inside "**auth**or"; a fixed vocabulary cannot cover how
people phrase things, and at plan time — when nothing has changed yet — that
guess decided everything. So read the task and pick.

The bar: **a lens earns its place only if the job needs that judgement.** Name
why in one clause. Loading all nine is the same failure as loading none, because
a list that is always the same carries no information.

## 3. Decide the conditional roles yourself

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

## 4. Tell the user the crew before running it

State it plainly in one or two lines — which lenses, which roles, and why. This
is the point where they can say "you have missed the database side of this",
which is far cheaper now than after the review.

> Crew: **database** + **backend** + **frontend** lenses (schema.ts, route.ts,
> page.tsx). Roles: reuse-auditor, correctness, bloat, perf. Skipping security —
> nothing here takes user input.

## 5. Run them in parallel

Launch the selected roles as subagents **in a single message**, so they run
concurrently rather than one after another, and **wait for them — pass
`run_in_background: false`.** Agents background themselves by default, which
would leave you idling for notifications instead of collecting findings.

**A role only knows what you tell it.** It starts in an empty context: it cannot
see this conversation, the plan, the diff, or what you already ruled out. A thin
brief is the most common reason a review comes back shallow, and it looks
identical to a change with nothing wrong with it.

Every brief carries all five:

1. **Where to look** — the diff range, and the new files, because untracked ones
   do not appear in `git diff`.
2. **What the change is for**, in two lines. Without intent it reviews the code
   against itself and reports style.
3. **The contract**, if one exists — what was agreed, and what was explicitly out
   of scope, so it does not report the omissions you both chose.
4. **What is already settled** — decisions taken, findings already refuted. Left
   out, it re-litigates them and you pay twice.
5. **What you specifically suspect**, if anything. A named suspicion is the
   cheapest thing you can give it.

## 6. Refute before reporting

Every finding goes to the `harness:refuter` agent before it reaches the user, and only
survivors are reported. This is not ceremony. A reviewer asked to find problems
will produce some whether or not they exist, and acting on an invented one adds
a defensive branch for a case that cannot happen — which is precisely the
over-building this harness exists to prevent.

Run the refutations in parallel too.

## 7. Report

One list, most severe first. For each: what is wrong, where, and the concrete
consequence. Say explicitly what was checked and came back clean — "correctness
and perf found nothing" is information, and without it a short report is
indistinguishable from a shallow one.

If everything was refuted, say so. Finding nothing on sound work is a real
result, not a failure to look hard enough.
