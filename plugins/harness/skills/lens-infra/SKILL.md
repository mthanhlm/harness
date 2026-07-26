---
name: lens-infra
description: Configuration, CI and shell judgement — secrets, environment handling, container and workflow definitions, and scripts that fail safely. Loads automatically on Dockerfiles, CI workflows, shell scripts and env or config files.
paths:
  - "**/Dockerfile*"
  - "**/docker-compose*.yml"
  - ".github/**"
  - "**/*.sh"
  - "**/*.bash"
  - "**/.env*"
  - "**/*.tf"
  - "Makefile"
user-invocable: false
---

# Infrastructure and scripting lens

## Secrets

A credential committed to git is compromised, and deleting it later does not
help — the history keeps it and so do any clones. Secrets come from the
environment or a secret store. `.env` files stay in `.gitignore`, and `.env.example`
carries the *names* with empty values so the next person knows what is needed.

A secret passed as a command-line argument is visible to anything that can read
the process list, and a secret echoed by a CI step is in the log forever.

## Shell scripts fail silently by default

Without `set -euo pipefail`, a script continues merrily after a failed command
and an unset variable expands to nothing — which is how `rm -rf "$DIR/"` becomes
`rm -rf /`. Start scripts with it.

Quote every expansion. `$FILE` breaks on spaces; `"$FILE"` does not. This is not
style; it is the difference between working and destroying the wrong path.

## Containers

Pin base images to a version, not `latest`, or the build is not reproducible and
a rebuild months later is a different image. Order layers cheapest-and-most-stable
first — dependency manifests before source — so a code change does not reinstall
the world. Do not run as root when the process does not need it, and do not copy
`.env`, `.git` or `node_modules` into the image.

## CI

A workflow that can be triggered by a fork and has access to secrets is a
credential leak with extra steps. Pin third-party actions to a commit rather
than a moving tag. Cache by a key derived from the lockfile, so a dependency
change actually invalidates it.

A CI step that cannot fail the build is decoration. If lint runs but its result
is ignored, either enforce it or delete it.

## Before changing config

Configuration errors do not surface until deploy, so read what is there and
change the minimum. Check whether the value belongs in an existing config
mechanism rather than a new one — a second way to configure the same thing means
two places to look and one of them will be wrong.
