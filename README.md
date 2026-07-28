# harness

A Claude Code plugin that blocks on facts and advises on taste.

One command — `/harness:plan` — takes a request from a vague sentence to reviewed,
working code, stopping once for your approval. Underneath, it blocks on facts
(syntax, types, tests) and advises on taste (design, bloat, docs), with every
judgement call delegated to a specialist running on a stronger model.

## Install

```bash
claude plugin marketplace add ~/lam/harness
claude plugin install harness@autonxt-harness
```

To work on the plugin without installing it, load it for one session:

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
| A worker finishes | Re-checks every file that worker touched, and only those |
| Turn ends | Full type-check, tests, build, and a scope check against the plan |
| Session ends | Records real token cost to the ledger |

## What you invoke

There is one entry point. Everything else the model loads for itself.

| Command | Does |
|---|---|
| `/harness:plan` | **Start here.** Understands the request, checks what exists, judges whether the code is worth building on, gets your approval — then builds and reviews it |
| `/harness:review` | Standalone review, for code you didn't just write (a PR, inherited code) |
| `/harness:report` | What recent sessions cost, and how often a gate caught something |
| `/harness:switch off` | Kill switch |

Hidden but model-invocable: `implement`, `crew`, `simplify`, `verify-tests`, and
the six `lens-*` domain skills. Ask for them in plain language ("check whether
these tests are real") and the model loads the right one.

## Which model runs what

One rule decides every model in the plugin:

> **Searching, comparing and pattern-matching are Sonnet work. Simulating an
> execution nobody wrote down is Opus work.** And whichever component does the
> high-volume work runs cheap, whatever else is true.

Subagents run on their own declared model regardless of the parent, so this holds
however you have your own session set. Measured: a Sonnet session spawning the
Opus `architect` billed both, $0.075 and $0.146, in one turn.

| Agent | Model | Why |
|---|---|---|
| `architect` | opus/xhigh | A wrong patch-or-rewrite verdict costs days. Runs rarely |
| `reviewer-correctness` | opus/xhigh | Must imagine inputs nobody wrote down |
| `reviewer-security` | opus/high | Adversarial thinking, but narrow and conditional |
| `reviewer-tests` | opus/high | "Would this fail if the code were wrong?" is simulation |
| `refuter` | opus/high | Last gate. A weak one throws away good findings |
| `reuse-auditor` | sonnet/high | Search and recall — CodeGraph walks the graph, not the model |
| `reviewer-bloat` | sonnet/high | Duplication and one-caller abstractions are patterns |
| `reviewer-perf` | sonnet/high | N+1s, missing indexes and blocking calls are structural |
| `reviewer-docs` | sonnet/medium | Compare the diff against the docs. Mechanical |
| `worker` | sonnet/medium | Executes a plan that was already agreed |

Every Sonnet agent has something checking it downstream: `reuse-auditor` feeds a
plan you approve, the three Sonnet reviewers pass through the Opus `refuter`, and
the worker is fenced by the plan and the per-edit gates. **Nothing on Sonnet makes
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

Three components — `plan`, `simplify` and the `reuse-auditor` agent — search with
CodeGraph. It follows calls instead of matching text, which is the difference
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

Indexing is fast (a few hundred milliseconds on a small repo) and `codegraph init`
writes a `.codegraph/` directory whose contents are self-ignored. It still leaves
one untracked entry, so either commit `.codegraph/.gitignore` or add
`.codegraph/` to the repo's own `.gitignore`.

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
lens skills that auto-load by file path; jobs live in role agents that run in
their own context. A role declares the lenses it needs, so a single reviewer can
hold frontend, backend, database and Python at the same time.

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

`/harness:report` reads real token counts from session transcripts — including
the cache-read and cache-write split, which is where most of the money usually
is.

For the harder question — whether a cheaper model with the harness beats an
expensive one without it — `evals/ab.py` runs the same task with and without the
plugin against a fresh fixture, and grades the result with tests the model never
saw:

```bash
python3 plugins/harness/evals/ab.py --model claude-sonnet-5 --runs 3
python3 plugins/harness/evals/ab.py --model claude-opus-5   --runs 3
```

**First measured result** (`slugify` case, Sonnet 5, 1 run per arm): both arms
passed; harness $0.242, bare $0.216. So on this task the harness cost about 12%
and changed nothing — the case is too easy to tell the arms apart. That is a real
finding about the *case*, not evidence either way about the plugin. A case that
discriminates needs to be hard enough that the bare arm sometimes fails: an
unfamiliar codebase, a change with non-obvious callers, or a task where the
tempting approach is wrong. Add those under `evals/cases/`.

`claude plugin eval --ablation with-without` does the same job with more
machinery, and is worth switching to if early access opens up on this account.

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
- **Edits made through `Bash` are invisible to every gate.** The hooks match
  `Edit|Write|MultiEdit|NotebookEdit`, so a change made with `sed -i` or a shell
  redirect never enters `files_touched`, is never checked, and never reaches the
  scope fence. Pre-existing, but it matters more now that `worker` subagents hold
  `Bash` and run unattended.
- **A repo's `.harness.json` can run arbitrary commands.** Its `checks` entries
  are adopted verbatim, and `argv` goes straight to `subprocess`. Cloning an
  untrusted repo and editing one file in it is enough. Read it before working in
  a repo you did not write.
- **`/harness:report` under-reports and double-counts.** It reads only the main
  session transcript, so every subagent — including workers, where the editing now
  happens — is invisible to it. It also appends one entry per `SessionEnd`, each a
  full re-read, so a resumed session is summed several times. Read cost off the
  usage meter instead until this is fixed.
- **`plugin eval` is gated to early access**, so `evals/ab.py` stands in for it.
- **Rust and Go support is written but untested** — neither toolchain is
  installed here. TypeScript and Python are verified against real repos.
