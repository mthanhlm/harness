> Requirements judgement — turning a request into a user story, a user flow and a
> data flow; finding what a requirement left out; and telling a mechanism apart
> from the outcome it is a means to. Load it before designing anything.
>
> Domain: requirements and product thinking

# Requirements lens

This is the lens for the moment before any code exists. Everything downstream —
the review, the type checker, the tests — asks whether the code matches the plan.
Only this asks whether the plan was worth having.

It matters more than it looks. Rework does not usually come from writing bad
code; it comes from writing correct code for a design that was wrong, discovering
that three files in, and changing the design while building it. Measured across
real sessions, lines changed *per file touched* rose 10 → 62 → 133 as sessions
got longer. That is not more work. That is the same files being rewritten.

## The central move: separate the mechanism from the outcome

A request almost always names a **mechanism**. The person wants an **outcome**.
They are not the same, and the gap between them is where the expensive mistakes
live.

| They asked for | They probably want | Which opens up |
|---|---|---|
| "add a cache" | it to be fast | fixing the N+1, an index, or precomputing |
| "add a retry" | it to stop failing | finding why it fails; retrying a non-idempotent write doubles the damage |
| "add a flag" | to not break existing users | a migration, or a version, or backwards-compatible defaults |
| "add a queue" | the request to return quickly | doing less work, or doing it later, or doing it in the same transaction and returning early |
| "add a dashboard" | to know when it breaks | an alert, which needs no dashboard and nobody has to look at |
| "make it configurable" | one specific behaviour changed | changing it, and deleting the option |
| "add an admin page" | to fix bad data occasionally | a script, one tenth the size, with no auth surface |

**The move**: say the outcome back in one sentence. Then ask whether the named
mechanism is the cheapest route to it. If it is not, that is the highest-value
thing you will produce all session.

**The failure**: implementing the mechanism perfectly. It ships, it works as
described, and the outcome does not arrive — because a cache does not help when
the query was slow for a reason caching hides.

## The user story, stated so it can be wrong

    <who> can <do what> so that <outcome>

Three parts, all load-bearing.

- **who** — a specific role, not "the user". "An admin" and "a customer" want
  opposite things from the same delete button. If you cannot name the role, you
  do not yet know whose problem this is.
- **do what** — an action they take, not a feature that exists. "Sees a status
  badge" is a feature. "Can tell whether their upload finished without asking
  support" is an action with a purpose.
- **so that** — the outcome. **If you cannot write this half, stop.** A story
  with no "so that" cannot be argued with, cannot be cut down, and cannot be
  verified. It is a feature request wearing a story's clothes.

<example name="a story that cannot be wrong">
"As a user, I want a settings page, so that I can change settings."

Circular. The outcome restates the mechanism. Nothing here tells you what to
build, what to leave out, or how you would know it worked.
</example>

<example name="the same request, made falsifiable">
"A team admin can change their workspace timezone without contacting support, so
that scheduled reports arrive in the morning rather than overnight."

Now the design has constraints: it needs to be self-serve (rules out a support
ticket), it needs to affect scheduling (so the change must propagate to already-
scheduled jobs — a question nobody had asked), and it is verifiable (schedule a
report, change the timezone, check when it fires).
</example>

## The user flow, and the three steps everyone forgets

Write what a person actually does, step by step: where they start, what they see,
what they click or send, what comes back.

Almost every first draft covers the happy path and stops. The three that get
left out, in order of how often they get left out:

1. **What they see while it is happening.** Anything slower than about a second
   needs an answer to "did my click work?" Without one, users click again — and
   now you have a double-submit problem you did not design for.
2. **What they see when it fails.** Not the error code. What they should *do*.
   "Something went wrong" is a dead end; "that email is already registered — sign
   in instead" is a flow.
3. **How they undo it.** Every destructive action needs an answer, even if the
   answer is "they cannot, so we confirm first."

**The tell in a plan**: a User flow section with no failure branch. Ask what
happens when the network drops halfway.

**"None — nothing a user experiences changes"** is a legitimate answer for
internal work, and it is not the same as leaving the section blank. Write it.

## The data flow, where wrong assumptions surface cheapest

What comes in, what it turns into, where it is stored, what reads it later. Name
the actual tables, endpoints, files and queues — not "the database".

This section is worth more than it looks because assumptions become visible here
while they are still free to fix. Specific questions to force:

- **Where is the source of truth?** If the same fact lives in two places, which
  one wins when they disagree? "They cannot disagree" is a claim to check, not an
  answer.
- **What is derived and what is stored?** A stored derived value needs an answer
  to "what recomputes it and when." A derived-on-read value needs an answer to
  "what does that cost at 100× the rows."
- **What reads this that nobody mentioned?** An export job, a report, another
  service, a mobile client on an old version. This is the most common source of
  "it worked and then something else broke".
- **What happens to data already in the system?** Every new required field has a
  backfill question. Every new constraint has an "are there existing rows that
  violate it" question. **A migration nobody wrote is the most common way a plan
  turns out to be twice its estimate.**

## What the requirement did not say

A requirement is written by whoever has the problem, not by whoever knows the
system. It is therefore incomplete by default — that is normal, not a failing,
and waiting for the requester to notice is not a plan.

Read the code, then work down this list. Each one is a question that has an
answer in the code, so asking the requester first wastes their time.

| Gap | The question |
|---|---|
| **Empty** | what shows before there is any data? First-run is a design, not an edge case |
| **One** | does the singular case look silly? "1 items", a chart with one bar |
| **Many** | what happens at 10,000? Pagination, or an unbounded query |
| **Concurrent** | two people, same record, same second. Who wins, and does the loser find out? |
| **Partial** | it half-worked. Is the system in a state anyone can reason about? |
| **Repeat** | they do it twice. Idempotent, or duplicated? |
| **Permission** | who *cannot* do this, and what do they see instead? |
| **Existing rows** | the data that is already there. Does it satisfy the new rule? |
| **Old clients** | a mobile app from three months ago. Does it still work? |
| **Deletion** | when the parent is deleted, what happens to this? |
| **Time** | timezone, DST, ordering, clock skew between services |
| **Money** | rounding, currency, and whether a float appears anywhere near it |

**The rule for what to do with each gap:** if two answers would change the scope
file list, the data flow or the user flow, ask. Otherwise decide it yourself and
**write down that you decided it**. A decision the requester never saw is one
they cannot correct, and they find out when the built thing is wrong.

Not asking is fine. Deciding silently is not.

## Ask well, or do not ask

Two failure modes, opposite directions, both common.

**Asking a non-engineer something they cannot have an opinion on.** "Write-through
or write-behind?" produces an arbitrary answer, which then launders a guess into
a requirement and removes it from the list anyone would have reviewed. Decide it
and record it.

**Asking about things the code answers.** Read first. A first round of twelve
questions where eight are answered by `grep` reads as not having bothered.

A question qualifies only if two different answers would change what gets built.
"It would be good to know" is not the test.

## Scope: name what you are NOT doing

The list of things you are deliberately leaving out is worth as much as the list
you are building, for one reason: it is what you check yourself against when a
tempting adjacent improvement appears three files in.

Write the neighbouring things that will look tempting mid-task. Be specific —
"not touching the auth middleware, even though it has the same bug" beats "no
refactoring".

**A plan with no exclusion list is indistinguishable from a plan that has not
thought about its boundaries**, and the second is what happens by default.

## Say what would falsify the design

Before building, write:

- If this design is wrong, what breaks first?
- What would show it, and when?

Not risks. Risks are unfalsifiable — "the migration could be tricky" can never be
checked against anything. A prediction names the failure and names the
observation:

> "Rows written between the deploy and the backfill get `status=NULL`, which the
> dashboard renders as `active` — visible as a count that does not match
> `SELECT count(*) WHERE status='active'`."

The point is not caution. It is that a design which stated what would break, and
then broke that way, is corrected in one move. A design that predicted nothing is
rediscovered from scratch on every attempt — which is the churn this whole lens
exists to prevent.

## Signals the requirement is not ready

Any two of these together mean stop and go back to the person:

- The "so that" is missing or restates the mechanism.
- Nobody can say what "done" looks like without looking at the implementation.
- The verification is "check it looks right".
- It names a solution and not a problem, and nobody present can say what the
  problem was.
- The same area has been changed three or more times already — the design is the
  suspect, not this request.
- A past decision recorded as `reworked` covers this shape. It was tried here.

## What this lens is not

Not a template to fill in. A story, a flow and a data flow written to satisfy a
format are worse than none, because they look like the thinking happened.

The test for every section is the same: **would two different answers here change
what gets built?** If not, one line is enough. If yes, that section is where the
work is.
