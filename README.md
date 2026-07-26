# harness

A Claude Code plugin that blocks on facts and advises on taste.

It intervenes at three points: before code is written (an agreed contract), while
it is being written (fast checks on every edit), and when the turn ends (project
verification). Judgement — design, bloat, tests, docs — comes back as advice from
a crew of specialists rather than as a gate.

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
| Prompt submitted | Nudges toward a contract when the request looks like implementation work |
| Before an edit | Past 3 files or 100 lines with no agreed contract, asks — **once per session** |
| After an edit | Format, lint, syntax and types on the touched file (~0.1–0.2s measured) |
| Turn ends | Full type-check, tests, build, and a scope check against the contract |
| Session ends | Records real token cost to the ledger |

## What you invoke

| Command | Does |
|---|---|
| `/harness:contract` | Agree scope, reuse, verdict and verification before coding |
| `/harness:crew` | Assemble the specialists a job needs, across every domain it touches |
| `/harness:review` | Review the diff with those specialists, refuting each finding first |
| `/harness:simplify` | Remove what isn't earning its place; fix comments and stale docs |
| `/harness:verify-tests` | Break the code on purpose and check the tests notice |
| `/harness:report` | What recent sessions cost, and how often a gate caught something |
| `/harness:switch off` | Kill switch |

## The two ideas it is built on

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

- **Contract approval is model-recorded.** The gate raises the cost of skipping
  the contract; it cannot make skipping impossible.
- **The end-of-turn gate can still block on inherited breakage** when a project
  check takes over two minutes, since the worktree baseline is skipped above that
  cost ceiling. Bounded by the three-block cap.
- **`plugin eval` is gated to early access**, so `evals/ab.py` stands in for it.
- **Rust and Go support is written but untested** — neither toolchain is
  installed here. TypeScript and Python are verified against real repos.
