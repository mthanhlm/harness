> Configuration, CI, container and shell judgement — secrets, environment
> handling, safe scripts, reproducible images, supply chain, deploys and
> rollback, and the failures that only appear in production. Loads automatically
> on Dockerfiles, CI workflows, shell scripts and env or config files.
>
> Domain: config, CI and shell

# Infrastructure and scripting lens

The distinguishing property of everything on this page: **the feedback loop is
long and the blast radius is wide.** A wrong line in a handler fails a test; a
wrong line in a deploy pipeline fails at 4pm on a Friday, in an environment you
cannot attach a debugger to, affecting everyone at once. So the standard here is
higher than the code's, not lower — and the things that pay off are the ones that
move a failure earlier: validate at startup, fail the build, pin the version,
make it reproducible.

## Secrets

A credential committed to git is compromised. Deleting it later does not help —
the history keeps it, every clone keeps it, and whatever mirrored or indexed the
repo keeps it. **Rotate it.**

- Secrets come from the environment or a secret store. Never a literal, never a
  committed file, never a default in code.
- `.env` files stay in `.gitignore`. `.env.example` carries the **names** with
  empty values, so the next person knows what is needed without learning a value.
- **A secret passed as a command-line argument is visible** to anything that can
  read the process list on that host. Use an environment variable, a file, or
  stdin.
- **A secret echoed by a CI step is in the log forever**, and CI logs are usually
  readable by more people than the secret store is. Masking helps and is not a
  guarantee — a base64'd or partially printed secret slips straight past it.
- **Rotation has to be possible.** A secret that cannot be rotated without
  downtime will not be rotated. Support two valid credentials at once so the
  overlap window exists.
- **Scope each credential to what it needs.** One admin token shared by six
  services means a compromise anywhere is a compromise everywhere, and you cannot
  revoke one use without breaking five.

## Configuration

- **Read config once at startup and validate it there.** A missing or malformed
  variable should stop the process at boot with a message naming it — not fail
  the first request that needs it, at 2am, in a code path nobody reads.
- **No secrets in defaults**, and no default that is silently wrong. A default
  `DATABASE_URL` pointing at localhost turns a missing variable into a confusing
  connection error instead of a clear one.
- **Environments differ in values, never in code paths.** `if (env ===
  'production')` around behaviour means what you tested is not what you shipped.
- **One mechanism per setting.** A value settable by env var, config file *and*
  CLI flag has a precedence order somebody has to know, and one of the three will
  be wrong. If you must have layers, make the resolved value loggable at startup.
- **Config is versioned with the code that reads it.** A deploy that needs a new
  variable must fail loudly if it is absent, not start and misbehave.

## Shell scripts fail silently by default

```bash
#!/usr/bin/env bash
set -euo pipefail
```

Without this, a script continues merrily past a failed command, an unset variable
expands to nothing — which is how `rm -rf "$DIR/"` becomes `rm -rf /` — and a
failure in the middle of a pipe is invisible because only the last command's
status counts.

Know the exceptions, so the flags do not surprise you: `set -e` does not fire
inside a condition, in a `||` chain, or in a function whose result is tested.
`grep` returning 1 for "no matches" will kill a script under `-e` — write
`grep … || true` when no match is a legitimate outcome.

**Quote every expansion.** `$FILE` breaks on spaces and glob characters;
`"$FILE"` does not. `"$@"` preserves arguments; `$@` splits them. This is not
style — it is the difference between working and deleting the wrong path.

Other things that bite:

- `cd` can fail. `cd "$dir" || exit 1`, or the rest of the script runs somewhere
  else entirely.
- `mktemp -d` for temporary files, with a `trap … EXIT` to clean up. A fixed path
  in `/tmp` is a symlink attack and a collision between two concurrent runs.
- **Check the tool exists** before using it. `command -v jq >/dev/null || { echo
  "jq required" >&2; exit 1; }` beats a cryptic failure three steps later.
- **Errors to stderr, exit non-zero.** A script that prints "ERROR" and exits 0
  is a green CI step.
- **Idempotent where you can.** Scripts get re-run, usually after a partial
  failure, usually by someone who does not know what the first run completed.
- Past roughly a hundred lines, or the first time you want a data structure,
  write it in a real language. Bash has no error handling worth the name.

Destructive commands deserve their own care: prefer an explicit path over a
variable, refuse to run when the variable is empty, and make `--dry-run` the
thing you test with.

## Containers

- **Pin base images to a version, ideally a digest.** `latest` means the build is
  not reproducible and a rebuild months later is a different image with different
  bugs. `python:3.12-slim` is better than `python:latest`; a digest is better
  still.
- **Order layers cheapest-and-most-stable first.** Copy the dependency manifest
  and install, *then* copy the source. Reversed, every code change reinstalls the
  world — and this is the most common reason a build takes eight minutes.
- **Multi-stage builds** so the compiler, the dev dependencies and the build
  cache do not ship. A smaller image is a smaller attack surface and a faster
  rollout.
- **Do not run as root.** A `USER` line costs nothing until the day it is the only
  thing between a container escape and the host.
- **Do not copy `.env`, `.git`, `node_modules` or credentials into the image.**
  Use a `.dockerignore`; it also makes the build context smaller, which is often
  where the slowness is. A secret in an intermediate layer is in the image even
  if a later layer deletes it.
- **One process per container**, and it should handle `SIGTERM`. A process that
  ignores it gets killed after the grace period, mid-request.
- **A healthcheck that checks something real.** A liveness probe hitting a
  handler that always returns 200 reports a healthy container with a dead
  database behind it.
- **Set resource limits.** Without them one container's memory leak takes the
  node down.

## CI

- **A workflow triggered by a fork must not have secrets.** `pull_request_target`
  and equivalents run with write access and repository secrets against attacker-
  authored code. This is the single most exploited CI misconfiguration.
- **Pin third-party actions to a commit SHA**, not a moving tag. A tag can be
  repointed by whoever owns the repo, and that action sees your secrets.
- **Cache by a key derived from the lockfile**, so a dependency change actually
  invalidates it. A cache key that never changes serves a stale cache forever;
  one that always changes is not a cache.
- **A step that cannot fail the build is decoration.** If lint runs and its result
  is ignored, either enforce it or delete it — the third state, "we run it and
  look at it sometimes", is the one where it silently degrades to always-failing
  and nobody notices.
- **The build must be reproducible from a clean checkout.** A pipeline that only
  works because of state left by the previous run fails the first time it matters,
  and it fails for the person least equipped to fix it.
- **Fail fast on the cheap checks.** Lint and type-check before a twenty-minute
  test suite.
- **Do not let CI be the only place things run.** A check nobody can run locally
  becomes a guessing game of push-and-wait.

## Deploys and rollback

- **Every deploy needs a way back.** Before shipping, answer: how do I undo this,
  and how long does it take? "Redeploy the previous version" is an answer only if
  the previous version still works against the current database — which is why
  migrations go first and separately, expand/contract style. See `lens-database`.
- **A migration and the code that needs it are two deploys**, not one. During any
  rollout both versions run at once.
- **Roll out gradually** where you can, and know what signal would make you stop.
  A deploy with no metric to watch is a deploy you find out about from users.
- **Feature flags decouple deploy from release** and are the cheapest rollback
  available — but a flag is code, it has both paths, and both need to work. Flags
  that never get removed become permanent untested branches.
- **Zero-downtime means the old and new versions are compatible in both
  directions**, including queue message formats and cache value shapes.

## Observability of the infrastructure itself

- **The logs must be somewhere you can query**, retained long enough to
  investigate something reported a week later, and scrubbed of the secrets and
  personal data that will otherwise end up there by accident.
- **Alert on symptoms users feel** — error rate, latency, queue depth, failed
  jobs — not on CPU. An alert nobody acts on is trained out within a month, and
  then the real one is ignored too.
- **Know what happens when the disk fills.** It is always the disk.

## Before changing config

Configuration errors do not surface until deploy, so read what is there and
change the minimum. Then check whether the value belongs in an existing mechanism
rather than a new one — a second way to configure the same thing means two places
to look and one of them will be wrong.

And ask what happens if this change is wrong: does it fail at boot, where you
will see it, or on the first request that touches it, where you will not?

## Review checklist

1. Any credential in the diff, in history, in a log, or in a CLI argument?
2. Is config validated at startup, or first used deep inside a request?
3. Does any behaviour branch on the environment name?
4. `set -euo pipefail` present? Every expansion quoted? `cd` checked?
5. Any fixed `/tmp` path, missing `trap` cleanup, or unguarded destructive command?
6. Does a failing script actually exit non-zero?
7. Base image pinned? Layers ordered so source changes do not reinstall deps?
8. Does the image run as root or contain build tooling, `.git` or a secret?
9. Does the container handle `SIGTERM`, and does its healthcheck check anything?
10. Can a fork trigger a workflow that holds secrets?
11. Are third-party actions pinned to a SHA? Is the cache key lockfile-derived?
12. Is there a CI step whose failure does not fail the build?
13. What is the rollback for this deploy, and does it work against the new schema?
14. Do both versions coexist during the rollout — API, queue messages, cache values?
