# harness

A Claude Code plugin that blocks on facts and advises on taste.

One command — `/harness:plan` — takes a request from a vague sentence to reviewed,
working code, stopping once for your approval. Underneath, it blocks on facts
(syntax, types, tests) and advises on taste (design, bloat, docs), with every
judgement call delegated to a specialist reading in a context of its own.

## Install

```bash
claude plugin marketplace add mthanhlm/harness
claude plugin install harness@autonxt-harness
```

Updates come from the remote, so a change is only live once it is pushed:

```bash
claude plugin marketplace update autonxt-harness
```

**Bump `version` in `plugin.json` with every change that must reach a running
session.** That command refreshes marketplace metadata but does not re-fetch a
plugin whose version it has already seen — it finds the version directory
present, leaves it, and reports success while the hooks go on executing the old
code. The failure reads as "I fixed it and nothing changed", so the next move is
usually to go and change correct code. `python3 -m pytest -m prepublish` fails
when anything under `plugins/` changes without the version moving — **run it
before publishing.** It is deselected from a plain `pytest` on purpose: it is the
only check here that asserts a property of the working tree rather than of the
code, so leaving it in the default run made every mutation test pass by
definition, and a sweep of eighteen scored eighteen against a suite that was
blind to several of them.

To develop against a local clone instead — edits take effect on the next session
with no push cycle:

```bash
claude plugin marketplace add ~/lam/harness
claude plugin install harness@autonxt-harness
```

Or load it for a single session without installing at all:

```bash
claude --plugin-dir ~/lam/harness/plugins/harness
```

## What runs automatically

| When | What happens |
|---|---|
| Session start | Detects how this repo is checked and says so, once |
| Prompt submitted | Nudges toward `/harness:plan` when the request looks like implementation work |
| Before an edit | Past 3 files or 100 lines with no agreed plan, asks — **once per session** |
| After an edit | Format, lint, syntax and types on the touched file (~0.1–0.2s measured) |
| A shell command runs | Records what it changed, so the scope fence can see it |
| A worker finishes | Re-checks every file that worker touched, and only those |
| Turn ends | Full type-check, tests, build, and a scope check against the plan |
| Session ends | Records real token cost to the ledger |

## What you invoke

There is one entry point. Everything else the model loads for itself.

| Command | Does |
|---|---|
| `/harness:plan` | **Start here.** Argues with the request, works out what you actually want, gets your approval — then builds and reviews it |
| `/harness:review` | Standalone review, for code you didn't just write (a PR, inherited code) |

Hidden but model-invocable: `implement`. Everything else that used to be a
skill is now either a script the flow runs or a page an agent reads — see
"Thinning the surface" below.

Two things you run by hand rather than as a command. `$CLAUDE_PLUGIN_ROOT` and
`$CLAUDE_PLUGIN_DATA` are **not** shell variables — Claude Code substitutes them
into skill and agent text and into hook commands, and nowhere else, so your own
terminal has neither. Set them once:

```bash
export CLAUDE_PLUGIN_ROOT=~/.claude/plugins/cache/autonxt-harness/harness/<version>
export CLAUDE_PLUGIN_DATA=~/.claude/plugins/data/harness-autonxt-harness

python3 "$CLAUDE_PLUGIN_ROOT/scripts/ledger.py"       # what sessions cost, and the rework figure
python3 "$CLAUDE_PLUGIN_ROOT/scripts/switch.py" off   # kill switch
```

`CLAUDE_PLUGIN_DATA` is the one that matters: the scripts read it to find the
ledger and the contracts, and without it they resolve a different directory from
the hooks and report a confident nothing. The report answered *"No sessions
recorded yet"* over a ledger holding ten sessions and $830.

## Which model runs what

One rule decides every model in the plugin:

> **Searching, comparing and pattern-matching are Sonnet work. Simulating an
> execution nobody wrote down is Opus work.** And whichever component does the
> high-volume work runs cheap, whatever else is true.

Subagents run on their own declared model regardless of the parent, so this holds
however you have your own session set. Measured: a Sonnet session spawning an
Opus judgement agent billed both, $0.075 and $0.146, in one turn.

| Agent | Model | Why |
|---|---|---|
| `challenger` | opus/xhigh | Decides whether the right thing is being built at all. A weak one here is unrecoverable |
| `designer` | opus/xhigh | Same. Everything downstream executes its conclusion faithfully |
| `reviewer-correctness` | opus/xhigh | Must imagine inputs nobody wrote down |
| `reviewer-security` | opus/high | Adversarial thinking, but narrow and conditional |
| `reviewer-tests` | opus/high | "Would this fail if the code were wrong?" is simulation |
| `reviewer-coherence` | opus/high | Holds the whole change at once to see a seam no single line shows |
| `refuter` | opus/high | Last gate. A weak one throws away good findings |
| `reviewer-bloat` | sonnet/high | Duplication and one-caller abstractions are patterns |
| `reviewer-perf` | sonnet/high | N+1s, missing indexes and blocking calls are structural |
| `reviewer-docs` | sonnet/medium | Compare the diff against the docs. Mechanical |
| `worker` | sonnet/medium | Executes a plan that was already agreed |

Every Sonnet agent has something checking it downstream: the three Sonnet
reviewers pass through the Opus `refuter`, and the worker is fenced by the plan
and the per-edit gates. **Nothing on Sonnet makes
a final call.**

**Your own session** is the one dial left. If your session does the editing, run
it on Sonnet — otherwise you pay Opus rates for the highest-volume work there is.
If you fan out to `worker` subagents instead, the editing is already on Sonnet and
the lead seat is free to be Opus.

One trap worth knowing: a **skill's** `model:` frontmatter only takes effect when
you type the slash command yourself. Loaded mid-turn, the override is nominal and
the work reverts to the session's model. Measured: a `model: haiku` skill driving
five Bash calls from an Opus session billed Haiku 17 output tokens and Opus 633.
This is why model policy lives on agents, not skills.

## CodeGraph — indexed for you, on first use

Two components — `plan` and the `challenger` agent — search with CodeGraph. It follows calls instead of matching text, which is the difference
between finding a helper named `toDisplay` when you searched for `formatName` and
writing the second copy of it.

**You do not have to index anything.** The index is per repository, so a global
CodeGraph install does nothing for a fresh clone — so those components run this
first, and it builds the index if there isn't one:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/codegraph_ready.py"
```

It is idempotent: already indexed, it does nothing. It also declines quietly
rather than surprising you — outside a git repo, or with CodeGraph not installed,
it says so and the search falls back to Grep and Glob.

Indexing is fast — a few hundred milliseconds on a small repo — and the
`.codegraph/` directory it writes is excluded for you (see below).

## Language servers — navigation on, diagnostics off

`.lsp.json` declares a language server for each of the four stacks the checks
already cover: TypeScript/JavaScript, Python, Go and Rust. What that buys is
go-to-definition, find-references and hover types — the things grep cannot do,
because grep finds a name and cannot tell a definition from a mention.

**The binary is not bundled.** Nothing here installs `pyright-langserver` or
`gopls`. A server whose command is missing is skipped, and Claude Code reports
why only in the `/plugin` Errors tab — so instead the session banner says it
once, and only when it applies to the repo you are actually in:

```
No language server for python, so code navigation falls back to grep.
`npm install -g pyright` enables go-to-definition and find-references.
```

**Every server sets `diagnostics: false`, deliberately.** The default is `true`,
which pushes every diagnostic for a file into the context after each edit —
including the errors that were already at HEAD. The per-edit check runs the same
compilers and reports only what your edit caused, and the banner tells the model
in as many words not to go fixing problems it did not cause. A second,
unfiltered stream of the same errors contradicts that on every edit. That
failure — a check firing on inherited breakage until someone switches the whole
thing off — is the one this plugin was built to avoid.

**Do not enable an official LSP plugin alongside this one.** `typescript-lsp`,
`pyright-lsp` and `rust-analyzer-lsp` claim the same extensions and ship the
default `diagnostics: true`. Only one server can own an extension: the first
registered wins and the other never starts, so which behaviour you get depends
on load order. `/plugin` names whichever is active. `gopls` has no marketplace
plugin, so Go has no conflict.

## A cloned repository does get to run its code

There is no approval step. Every check the detector finds runs, in every
repository, from the first turn — and some of those checks execute code that
arrived with the clone. `pytest` imports `conftest.py`, `go test` compiles
`_test.go`, `cargo check` runs `build.rs`, `eslint` loads `eslint.config.js`,
`npm run test` runs whatever `package.json` says today, and a tool resolved out
of `node_modules/.bin` or `.venv/bin` is a binary the repo shipped. Hooks are not
prompted for the way a Bash command is, so **opening someone else's repository
and working in it runs their code, with your permissions, with nothing in the
transcript.**

`.harness.json` is honoured on the same terms: a repository can add checks, and
it can switch its own checks off, by committing one file.

This was previously gated behind a per-repository approval (`/harness:trust`),
removed deliberately in favour of not having the step at all. The trade is
stated here rather than left for you to discover: what you get is that checks
always run and are never silently absent, which is the failure the approval step
twice caused. What you give up is the boundary.

`scripts/switch.py off` is the control that remains, and it is yours rather than
the repository's.

## Its own artifacts stay out of your product

`.codegraph/` and `.harness/` belong to the tooling, not to what you ship, so
session start adds them to `.git/info/exclude` — local to your clone, untracked,
invisible in every diff.

Deliberately **not** `.gitignore`: that file is tracked, so a hook editing it
would put a change in your next commit that you did not write, which is the
pollution being avoided. The cost is that it is per clone, so a teammate gets
their own. That is the right trade for a tool's scratch directories and the wrong
one for anything the project genuinely needs ignored — which is why it only ever
writes those two entries.

## It remembers what stays true

`.harness/lessons.md`, in your repo, excluded locally. `/harness:plan` and
`harness:challenger` read it before deciding anything, and a plan's contract can
carry a `## Lessons` section that a hook harvests into the file when the session
ends.

Without it every session starts from nothing: the same mistake gets made twice,
and a correction earned the hard way in one session is gone by the next. It is a
file rather than a feature on purpose, and you can edit it freely.

**The test for a lesson is whether it will still be true in three months.** A
status report about work currently in flight does not qualify — "finished the
retry queue this session" is not a lesson, it is the plan's own Verdict restated.
"The retry queue silently drops messages over 256KB" is one, because it stays
true regardless of what else changes around it.

A lesson that turns out wrong is not deleted — deleting it would let the same
mistake get made again with no trace it was ever caught. `lessons.py revise <id>`
records the correction beside the original entry instead, so the wrong one stays
visible and a plan proposing the same shape again has something to trip over.

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/lessons.py" show
python3 "$CLAUDE_PLUGIN_ROOT/scripts/lessons.py" add
python3 "$CLAUDE_PLUGIN_ROOT/scripts/lessons.py" revise <id>
```

## The closing report is a brief, not a wall of chat

A run used to end with the contract, the scope list and everything else typed
straight into the chat transcript — "a pile of text I can't follow," in the
words of the person who has to read it every time. Both skills now end in a few
short paragraphs: what changed, what was verified with the exact command and its
real result, and anything you have to decide. The contract is already on disk if
you want the rest.

A rendered HTML page and a published artifact were built for this and then
removed, at the same person's request, after they saw them. That is recorded
here because the idea is an obvious one to have again: if the closing report is
too long, the fix is a shorter brief, not a second place to put the long one.

## Something argues with the request before a plan exists

The old version of this ran *after* the plan was drafted, which meant it argued
about the plan's size. By then there was an author attached to it, and whether
the thing was worth building at all had been settled by nobody.

`plan` now starts by counting what can be counted — how many commits have
already rewritten the files you named, whether the repo can test itself — and
routes on it:

| Route | When | What runs |
|---|---|---|
| **A** | the goal is concrete *and* something automated can prove it | Nothing. Failing test, fix, review |
| **B** | one of those two is soft | The challenger, then one design |
| **C** | the goal is vague, or only a person can say it worked | The challenger, then **two competing designs** |

The **challenger** (opus) runs before anything is drafted and returns what the
user story actually is, what in the code contradicts the request (file and
line), what past decision it reverses, churn counts, whether the thing already
exists — and a verdict that includes *don't build this*. An objection carrying a
citation **blocks** and is put to you; one from judgement is relayed and
labelled advisory. That split is the point: a blocking question you open and
find unsupported teaches you to click through the next one.

On route C, two **designer** agents answer the same request under opposed
framings — smallest change that works, versus the structure this actually needs
— without seeing each other. The lead diffs them and presents **Agreed** and
**Diverged**. It does not pick, and there is no judge agent: every model here is
the same family, so a judge shares the blind spot that produced the
disagreement, and a judge that picks for you rebuilds the dynamic this exists to
stop.

## Thinning the surface

Fourteen skills carried `user-invocable: false`, which hides them from your
slash menu but **not** from the model-facing listing. That is 28 advertised
entry points for a plugin with one flow.

- The `lens-*` skills became pages under `references/lenses/`. They carry no
  tools and no model — they are knowledge, and being read as information is what
  they already were. How one gets picked is described below.
- `crew` folded into `review`; its lens-picking half became obsolete the moment
  agents started loading their own.
- `simplify` and `verify-tests` were deleted. Nothing invoked either.
- `report` and `switch` became scripts you run directly.

Skills went 17 → 3, and the listing 28 → 13. The contract template lost
*Surfaces touched*, *Reuse*, *Risks* and *What you did not say*; it gained
**Prediction** — what breaks if this design is wrong, and what would show it —
which is the one section that makes a design correctable in a single move
instead of rediscovered from scratch.

## Building in parallel

A plan whose Scope splits into slices that share no file is built by several
`worker` subagents at once instead of one thread going file by file. Two rules
make that safe — one enforced in code, one only instructed:

- **Every writer accounts for itself** — *enforced.* Each worker records what it
  changed in a file of its own, and readers merge. Before this, eight concurrent
  writers left one file and a tenth of the line count in the session state — and
  that state *is* the scope fence, so the end-of-turn gate would have reported
  clean on seven unreviewed slices.
- **A file belongs to exactly one worker** — *instructed, not enforced.* Two
  workers editing one file lose code with no error and no conflict marker;
  whoever saves last wins. `implement` and `worker.md` both say so plainly, and
  nothing stops a worker that ignores them. Assign disjoint files in the plan.

When a worker finishes, its own files are re-checked before it reports back. That
catches what the per-edit gate cannot: a later edit breaking a file the same
worker wrote earlier.

## The three ideas it is built on

**Never block on a problem the edit didn't cause.** A repo almost always carries
some pre-existing lint noise or a type error nobody has got to. A gate that
blocks on those blocks every edit, and gets disabled within a day. So when a
check fails, it is re-run against the file at `HEAD` — and at project scope,
against a detached worktree of `HEAD` — and only genuinely new diagnostics are
reported. This is the single most important behaviour in the plugin.

**One job needs several kinds of expertise at once.** Domain knowledge lives in
twelve lens pages under `references/lenses/`; jobs live in role agents that run
in their own context, so a single reviewer can hold frontend, backend, database
and Python at once.

Which lenses it gets was, for a while, decided purely by matching file paths, and
that was wrong in a way worth writing down. A path *correlates* with a domain and
does not determine one: `src/checkout/handler.ts` builds SQL from a request body
and matches no security pattern by name, and `internal/store.go` running a
migration matched nothing at all — so it was reviewed with no domain knowledge
and nothing said so. Matching harder does not fix that; the next proxy has the
next silent miss, and somebody maintains the patterns forever.

So the split is now by what is actually knowable where:

| | Decided by | Because |
|---|---|---|
| Language lens | `SubagentStart`, from the extension | a `.py` file **is** Python. A fact, not a guess |
| Subject lens | `SubagentStart`, from the agent | `reviewer-security` always needs security |
| Domain lens | **the agent, reading the diff** | only the thing holding the change knows what it is about |

The agent gets the full catalogue at startup and picks. That used to be a line in
a brief, which is to say it was skipped exactly as often as instructions are — so
`SubagentStop` now blocks a reviewer that is about to report with no lens behind
it at all, and tells it where the pages are. Blocked once per agent, never twice,
because a transcript that lags is not a reason to trap an agent in a loop.

**A fresh context beats a smarter one, for reading.** Every role runs in a window
spent entirely on its own question, and returns a conclusion rather than its
reading. That is why delegation still pays when the agent is on a *cheaper* model
than the session that called it.

## Configuring a repo

Detection prefers what a repo declares — `package.json` scripts, `Makefile`
targets — over guessing, and only uses a per-file tool the repo has opted into
via a local install or a config file. A globally installed linter never gates a
repo that never asked for it.

Where the guess is wrong, `.harness.json` at the repo root corrects it:

```json
{
  "disable": ["lint"],
  "checks": [
    {"kind": "test", "argv": ["make", "test"], "scope": "project",
     "label": "make test", "blocking": true}
  ]
}
```

## Measuring whether it works

`scripts/ledger.py` reads real token counts from session transcripts — including
the cache-read and cache-write split, which is where most of the money usually
is. It also prints **lines changed per file touched, bucketed by session
length** — the rework figure the 0.8 flow exists to move. A file rewritten
thirteen times is not thirteen files' worth of work, and that ratio rising with
session length is the design changing while it is being built.

For the harder question — whether a cheaper model with the harness beats an
expensive one without it — the honest answer is that nothing in this repo
currently measures it. An earlier A/B rig (`evals/ab.py`) ran the same task
with and without the plugin and graded the result with tests the model never
saw, but it drove both arms through `claude -p`: headless mode cannot approve a
plan, and `implement` hard-stops without `status: approved`, so the crew never
launched and no subagent ran in either arm. Its one recorded result — harness
$0.242 vs bare $0.216, a 12% delta — is itself the proof: one Opus judgement
agent alone costs $0.146, so no fan-out happened in either run. It has been
deleted rather than kept as a false measurement. Any future answer to this
question has to come from the ledger on real sessions — see
`scripts/ledger.py` — not from a synthetic rig.

The eval cases it would have run (`evals/cases/`) stay, because they discriminate
on their own terms and are checked without spending on a model:
`tests/test_eval_cases.py` proves for every case that the visible tests pass on
the pristine fixture and the hidden grader fails on it, so a model that did
nothing cannot pass by accident.

`claude plugin eval --ablation with-without` would do the A/B job properly, and
is worth switching to if early access opens up on this account.

## Known limits

- **Plan approval is model-recorded.** The gate raises the cost of skipping the
  plan; it cannot make skipping impossible.
- **Headless runs can't approve.** The approval step needs an interactive
  session; in `-p` mode there is nobody to answer `AskUserQuestion`.
- **The end-of-turn gate can still block on inherited breakage** when a project
  check takes over two minutes, since the worktree baseline is skipped above that
  cost ceiling. Bounded by the three-block cap.
- **When a gate seems not to have fired, read `gate.log`** in the plugin data
  directory (`~/.claude/plugins/data/harness*/gate.log`). Every hook records why
  it decided what it decided. This is how the skip-after-block bug was found: a
  gate that silently does nothing looks exactly like a gate that found nothing.
- **Edits made through `Bash` are checked late, not immediately.** They now reach
  `files_touched` and so the scope fence, the per-worker check and the end-of-turn
  gate — but not the per-edit check, which needs a file path the shell does not
  provide. A shell edit is caught at the end of the turn rather than the moment it
  is made.
- **The ledger double-counts a resumed session.** It appends one entry per
  `SessionEnd`, each a full re-read of the transcript, so a session resumed
  several times is summed that many times over. Subagent cost — reviewers,
  judgement agents and workers — is counted, and reported apart from the lead's
  as `delegated to subagents`.
- **`plugin eval` is gated to early access.** No stand-in for it currently
  exists — see "Measuring whether it works" for why the earlier attempt was
  removed rather than kept.
- **Rust and Go support is written but untested** — neither toolchain is
  installed here. TypeScript and Python are verified against real repos.
