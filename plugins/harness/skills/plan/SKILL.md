---
name: plan
description: The way to start any real piece of work. Works out what you actually want, checks what already exists, judges whether the code is worth building on, gets your approval, then builds and reviews it. Use for features, refactors, migrations, and bugs with unknown cause — anything beyond a one-line edit.
argument-hint: "[what you want]"
effort: xhigh
allowed-tools: Read, Grep, Glob, Bash, Write, Edit, AskUserQuestion, Task, Skill, TodoWrite
---

# Plan, build, review

The request: **$ARGUMENTS**

This is the single entry point. You take this all the way from a vague sentence to
reviewed, working code, stopping exactly once — to get the plan approved before
anything is edited.

The person you are working with has said plainly that they are not a seasoned
engineer, that their requirements and designs are not always right, and that they
want to be told when that is the case. Treating a flawed request as a
specification is the failure to avoid here, not the safe option.

## Where the expensive thinking happens

Judgement is delegated to subagents, and the reason holds whatever model you are
running on: **each one reads in a context of its own and returns a conclusion.**
Your context is already full of this conversation, which is exactly what makes it
a poor place to search a codebase or read a diff cold. Theirs is empty and spent
entirely on the question.

Some of them also run on a stronger model than yours, and some deliberately run
on a cheaper one — searching and comparing do not need what simulating an
execution needs. Either way, **use them rather than substituting your own
opinion**:

| Subagent | For |
|---|---|
| `harness:challenger` | Should this be built, and is it the right thing? Runs **before** the plan. |
| `harness:designer` | Route C only. Run **twice** under opposed framings; their divergence is the decision. |
| `harness:reviewer-*` | The review at the end. |
| `harness:refuter` | Kills weak findings before they reach the user. |

**The `harness:` prefix is required — for the skills below as much as these
agents.** Everything here ships inside a plugin, so both the Task tool and the
Skill tool address it by a scoped name: `harness:challenger`, not `challenger`;
`harness:implement`, not `implement`. A bare name does not resolve, and the
observed failure is not an error message — it is falling back to `Read` on the
skill's own `SKILL.md`. That looks like it worked. It is not the same thing: a
read document is information, an invoked skill is instructions.

Launch independent ones in a **single message** so they run concurrently, and
**wait for them — pass `run_in_background: false`.** Subagents run in the
background by default, and you cannot draft a plan without their findings. A
backgrounded agent here leaves you idling for a notification instead of working.

## Stage 0 — How much argument has this request earned?

Run this first, before reading anything:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/triage.py" "$ARGUMENTS"
```

It counts what can be counted — which named files exist, how many commits have
already rewritten each, whether this repo can test itself at all — and then
either forces a route or hands you the readings and the decision rule.

**It deliberately does not read the request's language.** Whether a goal is clear
is your judgement, and when the output says `route: YOURS TO DECIDE` you make it
on one question: *does anyone know what done looks like?*

| Route | When | What runs |
|---|---|---|
| **A — fix** | the goal is concrete **and** something automated can prove it | Nothing below. Write the failing test, fix it, review. No contract, no interview, no subagents. |
| **B — change** | one of those two is soft | Stages 1–6, one design. |
| **C — design** | the goal is vague, **or** only a person can say it worked | Stages 1–6, and stage 2b is not optional. |

Two mistakes, and they cost differently. Routing a small request to C is how a
process gets bypassed — the person you are working with has said outright that
the current flow feels like a hard process, and a design debate over a rename is
exactly what they mean. Routing a genuinely open question to A is how a session
runs nine hundred turns rewriting the same four files. **When it is close, the
tiebreaker is whether a person has to look at the result to know it worked.** If
they do, it is not A.

Say which route you took and why, in one line, before you start. If you take A on
something the triage output argued about, say that too — you are allowed to
overrule it, not to do so silently.

## Stage 1 — Understand what is actually wanted

Read the request again and work out what the person is trying to achieve, not
just what they typed. Then go and read the code — you cannot plan against a
codebase you have not looked at.

Before searching, make sure the index exists — it is built per repository, so a
fresh clone has none even with CodeGraph installed globally:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/codegraph_ready.py"
```

Read what this project has already learned, before deciding anything:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lessons.py" show
```

`show` prints every lesson still recorded, each with its id. Lessons are
durable — things that stay true — not a log of one entry per session, so the
list stays short enough to read in full rather than needing an index to skim.

Every session otherwise starts from nothing — the same mistake gets made twice
and the same correction gets rediscovered from scratch. If a lesson names
something this request touches, say so out loud rather than silently repeating
it. And if this request would reverse what a lesson says, that is worth
raising: a lesson is evidence, not an instruction, but reversing one by
accident is how a codebase relearns the same lesson twice.

**A lesson that was later revised is the strongest evidence you have.** It
means something of this shape was already tried here and turned out wrong —
the correction sits beside the original entry rather than replacing it. Say so
before proposing the same shape again.

When this plan genuinely reverses a lesson, open the replacement bullet in
`## Lessons` with `supersedes <id>:` — for example `- supersedes L3: releasing
takes three commands now, and the last is a restart`. The hook that harvests the
section does the rest: the old lesson stays on record, marked, with the
correction pointing back at it, instead of leaving a lesson and a plan that
quietly disagree. `lessons.py revise <id>` does the same thing by hand when you
are not writing a contract.

The declaration rides on the bullet on purpose. It used to be a `Supersedes:`
header at the top of the contract, which named the entry that died but never the
text that replaced it — so the pairing had to be reconstructed by hand at the end
of a long turn, and it never was.

It builds the index if it is missing, does nothing if it is already there, and
says so plainly if CodeGraph is unavailable. Relay its line to the user when it
had to build one. Then search:

```bash
codegraph explore "<the capability in question>"
```

If it reported that CodeGraph is unavailable, fall back to Grep and Glob against
the vocabulary the codebase would plausibly use.

## Stage 1b — Have the request argued with, before there is a plan to defend

**Routes B and C only.** On route A, skip this entirely and go build.

Launch `harness:challenger` with the request and what you have read so far. Wait
for it — `run_in_background: false`.

This is the stage the whole flow exists for, and its position is the point. The
old flow argued *after* the plan was drafted, which meant arguing about the
plan's size; by then there was an author attached to it and the question of
whether the thing was worth building at all had quietly been settled by nobody.
The challenger runs while that question is still open and it is briefed to
answer it: what the user story actually is, what in the code contradicts the
request, what past decision it reverses, how many times this mechanism has
already been rewritten, whether it exists already, and a verdict that includes
**don't build this**.

**Relay what it returns, in two piles, and keep them apart.**

- **Blocking** — objections carrying a citation: a file and line, a lesson
  id, or a churn count. These go to the user through `AskUserQuestion`
  before you draft anything. They have to answer; you do not get to decide on
  their behalf that the objection was minor.
- **Advisory** — objections from judgement, with no evidence behind them. Relay
  them **labelled as judgement**, and carry on. They do not stop anything.

**Never promote an advisory objection into the blocking pile.** The tiers are not
confidence levels — they are whether the user can go and check. A blocking
question they open and find unsupported teaches them to click through the next
one, and then the mechanism is gone and the whole stage is theatre.

Then argue back yourself where you disagree. The challenger read the code cold
and has no memory of this conversation; it is wrong sometimes, and a finding you
have already disproved should be said so, not relayed. What must not happen is
the objection quietly disappearing.

**Their answers are the start of the interview, not a substitute for it.** A
blocking objection they overrule goes into the contract's Disagreement section
with their reason — on the record. If it will still hold true past this
session, it belongs in the contract's `## Lessons` section too, so the next
session inherits it instead of relitigating it from scratch.

## Stage 1c — Interview

**Interview them, until nothing material is unresolved.** A requirement is
written by whoever has the problem, not by whoever knows the system, so it is
normally incomplete — and the person you are working with has said outright that
they cannot tell what they left out. Waiting for them to notice is not a plan.

**Read the code first.** This is a precondition, not a preference: it is what
stops the first round being twelve questions the codebase already answers.

Then ask in rounds, with these four bounds:

- **A question qualifies only if two answers would change the Scope file list,
  the Data flow, or the User flow.** Anything smaller, decide yourself and write
  it down. This is the whole test — "it would be good to know" is not one.
- **Up to four questions per round**, since `AskUserQuestion` takes four. One at
  a time is what turns an interview into an interrogation.
- **Every round offers "use your judgement for the rest"**, which ends the
  interview immediately and moves everything outstanding into *Disagreement*.
  They must always be able to stop without knowing anything.
- **Stop at three rounds**, or sooner when a round changes nothing in the draft.
  A fourth round means you are asking the code's questions, not theirs.

Two failure modes to avoid, in both directions. Asking a non-engineer something
they cannot have an opinion on — "write-through or write-behind?" — is worse than
deciding it yourself, because an arbitrary answer launders a guess into a
requirement and removes it from the list they could have reviewed. And if a round
returns nothing at all (a headless run has nobody to answer), treat every open
question as answered by your recommendation, record them all, and proceed.

The interview closes before the plan is presented. Stage 3 is approval, not
another round.

**But write down every gap, whether or not you ask about it.** The person you are
working with has said they do not always know what a requirement is missing, and
that is the normal case rather than a failing — a requirement is written by
whoever has the problem, not by whoever knows the system. So as you read, keep a
list of what the request did not say: the case it does not cover, the existing
behaviour it would change without mentioning it, the thing it implies but never
states. Each one goes in **Disagreement** as one line — the assumption you made,
and what changes if it is wrong.

Not asking is fine. Deciding silently is not — a decision the user never saw is
one they cannot correct, and they only find out when the built thing is wrong.

## Stage 1d — Two designs, and you present the difference

**Route C only.** On routes A and B, skip this and draft the plan yourself —
running it everywhere is the ceremony that gets the whole flow bypassed.

Launch `harness:designer` **twice, in a single message**, with the same request
and opposed framings. Wait for both — `run_in_background: false`:

- **A — the smallest change that could work.** Minimise new structure.
- **B — the structure this actually needs.** Treat the existing shape as
  evidence, not a constraint.

Neither sees the other. That independence is the entire value: two samples drawn
separately can disagree, and where they disagree is a decision that would
otherwise have been made silently by whoever drafted first. A second designer
that read the first can only lose information relative to it, never add any — so
do not summarise one into the other, and do not run them in sequence.

### Diff them, and do not pick

Go heading by heading — Goal, User flow, Data flow, Scope, Verification,
Prediction — and sort every point into one of two lists.

- **Agreed** — both designs said the same thing. One line each, stated flatly.
  This was forced by the problem and there is nothing to decide.
- **Diverged** — the designs answered differently. Give **both answers and both
  reasons**, in the designers' own terms, and say which you would choose and why.

Then `AskUserQuestion` on the divergences — per point where they are independent,
or as a whole where choosing A's data flow and B's scope would produce something
neither designer would sign. Say which of those two it is.

**You do not resolve the divergence and neither does a third agent.** Every model
here is the same family, so a judge shares the blind spot that produced the
disagreement, and a judge that picks for you rebuilds the thing this flow exists
to stop — the assistant deciding and the user agreeing, in the other direction.
Your recommendation goes first because you have read both and they have not.
The choice is theirs.

**An empty divergence list is a good outcome, not a failed stage.** It means the
design was forced. Say so in one line and move on; do not go looking for a
difference to justify having run two agents.

The chosen design becomes the plan. Points where they agreed need no further
argument — carry them into the contract as written, including the **Prediction**,
which a later session will judge against what actually happened.

## Stage 2 — Draft the whole picture

Write this file, exactly this structure, to the path the harness printed at
session start — the line beginning *"This session's plan contract belongs at
exactly"*. If it is no longer in view, ask for it:

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/contract.py" path
```

The environment assignment is not decoration: a shell does not inherit
`CLAUDE_PLUGIN_DATA`, so without it the script resolves a different directory
from the one every hook uses and prints a confident path to a file nothing reads.

**Use that exact path and no other.** The scope fence reads that one file: a plan
written under any other name leaves `status:` unread, the fence empty, and the
end-of-turn gate certifying every edit in the session as agreed. If the command
above cannot name the path it says so and exits non-zero — it never guesses, and
neither should you.

```markdown
# Plan: <one line>

status: pending
verdict: patch | refactor-first | rewrite | don't build this

## Goal
<What the person is trying to achieve, in their terms, not yours. One paragraph.>

## User flow
<What a person actually does, step by step, once this exists. Where they start,
what they see, what they click or send, what comes back. If nothing a user
experiences changes, say so — internal work is allowed to say "none".>

## Data flow
<What moves where. What comes in, what it turns into, where it is stored, what
reads it later. Name the actual tables, endpoints, files and queues. This is
where wrong assumptions surface earliest.>

## Scope
Files this will change:
- path/to/file.ts — what changes in it
<**This list is parsed by the end-of-turn gate, and anything it cannot read is
not protected.** One literal repo-relative path per bullet, with its extension,
as the first thing after the dash. No globs (`agents/*.md`), no braces
(`skills/{a,b}/SKILL.md`), no two paths on one line — each of those silently
parses to nothing, and a file left out of the fence can be rewritten with the
gate still reporting clean. If that makes the list long, the list is long.>

Also name the failing test's own file here. The build is told to write it first,
and the fence will otherwise flag it as an unagreed change.

Slices:
- worker 1 — path/to/a.ts, path/to/b.ts
- worker 2 — path/to/c.ts
<**Work this out every time the list runs past two files, and write what you
concluded either way.** Do not leave the section off: a plan that is silent here
is indistinguishable from one that considered a split and declined, and the
silent version is what happens by default — across a full day of real use, no
plan ever wrote this line, so the workers never ran once.

Group the files by what they depend on, then say which groups share nothing. Two
groups that share no file can be built at the same time; a chain where each step
needs the last cannot, and "serial, because X must land before Y" is a complete
and correct answer to write here.

Every file appears under exactly one worker. Two workers sharing a file lose code
silently — whoever writes last wins, with no error and no conflict marker.>

Explicitly NOT changing:
- <the neighbouring things that will look tempting mid-task>

## Verdict
<Which of the four, and why — the challenger's verdict, where you overruled it,
what it found already exists, and the churn count on the mechanism this touches.
Two or three sentences.>

## Disagreement
<Where the request is wrong and what you would do instead — or "None."

**Every blocking objection the user overruled goes here, with their reason**, and
so does every gap you noticed and decided yourself rather than asking about. Both
are decisions the user never got to make. Leave it out and the same argument
happens again in a month, from scratch, by someone with no idea it was ever
had — write in `## Lessons` below whichever of these will still be true past
this session, so it survives rather than sitting here for nobody to read again.>

## Lessons
<Optional. What this session learned that will still be true in three months —
not a status report about work currently in flight. A hook harvests this
section when the session ends, so what qualifies has to survive the session
ending: "the retry queue silently drops messages over 256KB" is a lesson;
"finished the retry queue this session" is not one, it is the Verdict restated.
Omit the section entirely when there is nothing that will outlive this session —
most plans have nothing here, and that is fine.>
- <One dashed bullet each, written as `Title: what was learned.` The title is
  the text before the first colon, and it is what the next session sees in its
  index — so `the retry queue drops messages over 256KB: the broker caps a
  frame and the producer never hears about it` reads there; `detect.py: fixed`
  does not.>
- <supersedes L3: open a bullet this way when it corrects a lesson already on
  file. The old one is kept and marked, not overwritten.>

## Budget
~N files, ~N lines.
<**Write this every time, and mean it.** It is not decoration: a plan that
predicted four files and a hundred lines, executed as nine files and nine
hundred, was not built — it was redesigned while being built, and that is the
one thing this flow exists to catch. What actually changed gets compared
against this line at the end, so write it and mean it.>

## Verification
Command that proves this works: `<exact command>`
Failing test to write first: `<test name and what it asserts>`

## Prediction
- If this design is wrong, what breaks first: <the specific failure>
- What would show it: <the observation that would reveal it, and when>
<**Write what would falsify the design, not what might go badly.** "The migration
could be tricky" predicts nothing and can never be checked; "if requests arrive
out of order the second write silently wins, visible as a stale `updated_at`
under concurrent edits" names the failure and names what you would see. This is
what stops a design being rebuilt three times: one that stated what would break,
and then broke that way, is corrected in one move. One that predicted nothing is
rediscovered from scratch on every attempt. On route C, carry the designers'
prediction across rather than writing a new one.>
```

Two sections do the most work and are the ones people skip. **"Explicitly NOT
changing"** is what you check yourself against when a tempting adjacent
improvement appears three files later. **"Data flow"** is where a
misunderstanding becomes visible while it is still free to fix.

On **Disagreement**: if you genuinely have none, write "None." and move on. Do not
manufacture an objection to look rigorous — an invented concern trains the reader
to skim, which is exactly when the real one gets missed.

Stage 0 already counted how many times the mechanism this plan touches has been
rewritten. **Carry that count into the Verdict**, and carry what it means with
it: a third or later patch to the same mechanism is evidence the design is wrong,
not that this patch is wrong. A number with no rule attached changes nothing.

Before you present it, read the plan back against what the challenger said and
check that nothing quietly went missing. An objection acted on is cut; an
objection overruled is one line in Disagreement. An objection that appears in
neither was not answered — it was forgotten, and it is about to be built.

## Stage 3 — Get approval, and actually stop

Present the goal, the user flow, the data flow, the verdict and the budget —
and **read out Disagreement**. That section is the one the user cannot supply
themselves: it holds what they did not know to mention, what you decided on their
behalf, and what the challenger objected to. Two lines of it are worth more than
a paragraph restating what they already asked for.

Then `AskUserQuestion` with real choices, your recommendation first:

- proceed as planned
- proceed with your recommendation instead (when you disagreed)
- change the scope
- do not build this

**Wait. Do not edit anything before the answer.** This is the one interruption in
the whole flow, and it is the point of the flow.

When they answer, set `status: approved` and record their choice in the
Disagreement section if it differs from what you recommended. If they overrule
you, build what they asked for properly and without sulking — the objection is on
the record, and that is enough.

## Stage 4 — Build it

Invoke `harness:implement` with the Skill tool and follow it — the scoped name,
not `implement`. It holds the rules for building against an approved plan:
failing test first, stay inside the scope fence, reuse what the plan said to
reuse, fix what the automatic checks report.

The checks run on their own as you edit. A check that fires has already confirmed
the problem is new — other diagnostics in the same file predate you and are not
yours to fix.

## Stage 5 — Review it

When the build is done and the verification command passes, invoke
`harness:review` with the Skill tool — again the scoped name. It picks the
specialists this particular change needs, runs them in parallel — each in a
context spent only on the diff — and refutes each finding before reporting.

Do not skip this because the work looks fine. Looking fine is what a defect does.

## Stage 6 — Report, and leave a record

The complaint this stage exists to answer, in the user's own words: the end of a
run used to hand them "a pile of text I can't follow." So two things end the run
now, not one long report — a short prose brief in the chat, and a link to a page
holding everything the brief left out.

### The page

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report_page.py" write
```

This prints the absolute path of a self-contained HTML page rendering the
contract, the recorded lessons and this session's state, and writes a second
file beside it with `.fragment.html` in place of `.html`.

**Publish the `.fragment.html` one** with the Artifact tool, and hand the user
the link it returns. The two files hold the same body; the printed one is a
complete document so it opens from `file://` and from the status line, and the
fragment carries no frame of its own because the Artifact tool supplies one.
Publishing a complete document through it nests one page inside another.

Publishing the same path again later in the session updates the same artifact
rather than minting a new one, so the link the user already opened stays live —
that stability is the reason this exists, not a side effect of it.

The full contract, the scope list and the lessons all live on the page. Do not
restate them in chat — repeating the page in prose is the pile of text again,
just reformatted.

### The brief

A few short paragraphs, not a template. Say what changed, what was verified —
the exact command and its real result — and anything the user has to decide.
Plain bullets where a list genuinely helps. No box-drawing, no column alignment,
no layout that depends on how wide the user's terminal happens to be: an earlier
version of this stage specified a fixed ASCII block and it was rejected on sight
as unreadable. Short and plainly written is what was actually asked for.

**"Found nothing" and "did not report" are different results, and only one of
them is good news.** The review skill names any role whose findings never
arrived. That sentence belongs in the brief itself, not only on the page — it is
something the user has to know, not a detail:

> reviewer-correctness did not report, on two attempts. Nothing here covers
> correctness — the verification command passing is not a substitute.

If the review found nothing, say so plainly in the brief. Sound work reviewing
clean is a result and earns its place in two lines, not just a mention on the
page.

Anything you deliberately left alone because it was out of scope belongs on the
page, not the brief — it is the backlog the scope fence protected you from
wandering into, not something the user needs to act on right now.

A hook harvests the contract's `## Lessons` section when the session ends, so
what actually gets inherited by the next session is whatever you wrote there
while drafting the contract — not the Disagreement section, and not anything
said for the first time here.
