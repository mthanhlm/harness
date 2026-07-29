---
name: trust
description: Approve the commands a repository supplies for itself, so the harness may run them. Use when session start says checks are being withheld, when a repo's own tests and linting are not running, or when the user asks what this repository would execute.
argument-hint: "[status|grant|revoke]"
effort: low
allowed-tools: Bash, Read
---

# Trust this repository's own commands

Action: **$ARGUMENTS** (show status if empty)

Most of what the harness runs it composed itself — `py_compile`, `node --check`,
`bash -n`. Only the file path comes from the repository, so those are safe
anywhere and always run.

Some of it the repository wrote: `.harness.json` check entries, `package.json`
scripts, and any tool resolved out of `node_modules/.bin` or `.venv/bin`. Those
are files that arrived with a clone, and running them is running a stranger's
code with the user's full permissions. They wait for this.

## 1. Show what is being asked for

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trust.py" status
```

**Show the user the actual commands, in full, before anything else.** That is the
entire value of this step. A summary like "3 checks" gives them nothing to judge;
`sh -c "curl … | sh"` gives them everything.

If there is nothing repo-authored, say so and stop. Most repositories define
nothing, and manufacturing a decision here would train them to approve without
reading, which is exactly the habit that makes this useless.

## 2. Read them before recommending

You can judge these and the user often cannot, so do it rather than passing the
buck. Look for what a check has no business doing: reaching the network,
writing outside the repo, reading credentials, piping something into a shell,
or being obfuscated. A test runner is a test runner; `curl | sh` is not.

Say plainly which it is. "These look like ordinary project tooling" is a useful
sentence. So is "the second one downloads and executes a script — I would not
approve this."

## 3. Approve, or do not

```bash
CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trust.py" grant
```

The approval is recorded against the exact command set. If the repository later
changes what it runs, it comes back here — approving once is not a standing
grant on whatever it decides to do next.

`revoke` undoes it.

## 4. Say what changed

After approving, the repo's own tests, linting and type-checking start running
at the end of a turn. Before approving, they do not, and the harness is checking
less than it appears to be. Either way the user should know which of the two
they are in.

Never approve on the user's behalf without showing them the commands. The point
is not the record — it is that somebody looked.
