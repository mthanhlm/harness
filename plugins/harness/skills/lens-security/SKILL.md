---
name: lens-security
description: Security judgement for code being written — trust boundaries, injection, authorisation, secrets and what errors give away. Loads automatically in auth and middleware directories and on env files.
paths:
  - "**/auth/**"
  - "**/*auth*.ts"
  - "**/*auth*.py"
  - "**/middleware/**"
  - "**/.env*"
user-invocable: false
---

# Security lens

This loads for whoever is **writing** the code, which is the point. A review
finds a vulnerability after it exists; this is the knowledge that stops it being
written. `reviewer-security` carries this same lens rather than restating it.

## Name the trust boundary before anything else

Every input crosses from somewhere you do not control to somewhere you do. Write
down where that line is, because every rule below is about what happens at it.

Untrusted does not only mean "typed by a user". It means request bodies, query
strings, headers, cookies, uploaded filenames, webhook payloads, third-party API
responses, environment variables in a repo you cloned, and anything read back
out of your own database that an untrusted party once put there.

## Authorisation is per-object, not per-route

Authentication asks *who are you*. Authorisation asks *may you touch this
particular row*. A route that checks a session exists and then loads
`invoice/:id` from the path lets anyone enumerate every invoice. Check ownership
at the point of the fetch, in the query itself where possible, so it cannot be
forgotten by the next caller.

Deny by default. A permission check that only runs on the paths someone
remembered is a permission check on nothing.

## Never build an interpreted string from untrusted input

The same shape recurs across every interpreter:

- **SQL** — parameterised queries, always. String interpolation into SQL is the
  bug, whatever escaping surrounds it.
- **Shell** — pass an argv list, never a command string. `shell=True` with any
  untrusted fragment is remote code execution.
- **Paths** — resolve, then check the result is inside the directory you meant.
  `../` and symlinks both escape, and a path checked before resolution is not
  checked.
- **HTML** — escape on output, contextually. `dangerouslySetInnerHTML` and
  `innerHTML` with anything user-derived is stored XSS.
- **Deserialisation** — `pickle`, `yaml.load` and their kin execute what they
  read. Use the safe loader and a schema.

## Secrets

Secrets come from the environment or a secret manager, never a literal, never a
committed file, never a default value in code. A secret that has been committed
is compromised even after the commit is removed — rotate it, do not just delete
it.

Do not log them, do not put them in URLs (they land in access logs and
referrers), and do not return them in an error.

## Errors and timing tell an attacker things

"User not found" and "wrong password" as distinct messages is an account
enumeration oracle. A stack trace in a response names your framework, your file
layout and often your query. Log the detail server-side, return something
useful and dull.

Compare secrets, tokens and signatures with a constant-time comparison — `==`
returns early and leaks the length of the matching prefix.

## Before adding an endpoint or a dependency

For an endpoint: state who may call it and what they may reach through it,
before writing the handler. For a dependency: it runs with your process's full
permissions — check it is the package you meant, and that its install scripts
are not the actual product.
