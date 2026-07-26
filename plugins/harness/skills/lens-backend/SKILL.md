---
name: lens-backend
description: API and service judgement — endpoint contracts, validation at the boundary, error and status semantics, authorisation, idempotency and background work. Loads automatically in api/, routes/, server/ and on route handlers.
paths:
  - "api/**"
  - "app/api/**"
  - "routes/**"
  - "server/**"
  - "**/route.ts"
  - "**/*_api.py"
  - "**/handlers/**"
user-invocable: false
---

# Backend lens

## Everything crossing the boundary is untrusted

Validate at the edge, once, into a typed shape — then the rest of the handler
works with something known rather than re-checking. A field the client controls
is a field an attacker controls: `userId` in a request body is a claim, not an
identity. Take identity from the session.

## Authorisation is per-resource, not per-route

"Is this user logged in" and "may this user touch this row" are different
questions, and only the second one prevents a user reading someone else's data
by changing an id. Every handler that takes a resource id needs the second
check, and it belongs next to the fetch, not in middleware that cannot see the
row.

## Status codes and errors carry meaning

400 for a malformed request, 401 for unauthenticated, 403 for forbidden, 404 for
absent, 409 for a conflict, 422 for a well-formed but unacceptable body, 500 for
"we broke". Returning 200 with an error in the body means every caller has to
know that.

Error responses must not leak internals. A stack trace or a raw database message
is reconnaissance for an attacker and noise for a client.

## Writes need to survive being retried

Networks retry. A POST that charges a card or sends an email must be safe to
receive twice — an idempotency key, or a uniqueness constraint that makes the
second attempt a no-op. This is the failure that only appears in production.

## Work that outlives the request

Anything slow — email, video processing, a third-party call — should not be
holding a request open. If it must be inline, it needs a timeout: an external
call with no timeout is an outage waiting for the other side to hang.

## Before adding an endpoint

Read a neighbouring handler and match its shape for validation, error handling
and response envelope. An endpoint that returns a different error format from
every other endpoint makes every client special-case it.
