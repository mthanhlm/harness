> API and service judgement — endpoint contracts, validation at the boundary,
> error and status semantics, authorisation, idempotency, pagination,
> transactions, background work, caching and observability. Loads automatically
> in api/, routes/, server/ and on route handlers.
>
> Domain: API and services

# Backend lens

A backend is a promise made to callers you cannot change and a promise made to a
database you cannot roll back. Most of what goes wrong here is one of those two
promises being broken quietly — a response shape that shifted, a write that
happened twice, a transaction that committed half of something.

Two pages sit next to this one and are not restated here: `lens-security` for
trust boundaries and injection, `lens-contracts` for versioning and breaking
changes. This page is the handler itself.

## Everything crossing the boundary is untrusted

Validate at the edge, once, into a typed shape. Then the rest of the handler
works with something known rather than re-checking, and every new caller inherits
the check instead of having to remember it.

A field the client controls is a field an attacker controls. `userId` in a
request body is a claim, not an identity — take identity from the session and
never from the payload. The same goes for `price`, `role`, `orgId`, `isAdmin` and
any timestamp you will treat as authoritative.

Validate the *shape* and the *domain*: a string that is 40MB, a page number of
`-1`, a date in the year 9999 and an array of 100,000 ids are all well-typed and
all attacks. Bound every list, every string and every number that reaches a
loop or a query.

## Authorisation is per-resource, not per-route

"Is this user logged in" and "may this user touch this row" are different
questions, and only the second stops someone reading another customer's data by
changing an id in the URL.

The check belongs next to the fetch — ideally inside the query, where it cannot
be forgotten by the next caller — not in middleware that cannot see the row. Full
treatment in `lens-security`; the backend-specific part is that every handler
taking a resource id needs it, including the export, the bulk endpoint and the
webhook.

## Status codes and errors carry meaning

| Code | Means |
|---|---|
| 400 | Malformed — could not be parsed or is missing required fields |
| 401 | Not authenticated. The client should get credentials |
| 403 | Authenticated and not allowed. Getting new credentials will not help |
| 404 | Absent — or hidden, when revealing existence is itself a leak |
| 409 | Conflict with current state — duplicate, version mismatch, already done |
| 422 | Well-formed, parsed fine, but semantically unacceptable |
| 429 | Rate limited. Include `Retry-After` |
| 500 | We broke. Never for a client mistake |
| 503 | Temporarily unavailable, dependency down. Include `Retry-After` |

Returning 200 with an error in the body means every caller — including every
monitoring tool, load balancer and retry policy — has to know that. It is the
single most expensive shortcut in this list, because it cannot be taken back
once clients depend on it.

**A consistent error body**, decided once for the service:

```json
{ "error": { "code": "insufficient_funds", "message": "…", "request_id": "01H…" } }
```

The `code` is for machines and must be stable — clients will branch on it, which
makes it part of your contract. The `message` is for humans and may change. The
`request_id` is what turns a support conversation into a log query.

Errors must not leak internals. A stack trace or a raw database message is
reconnaissance for an attacker and noise for a client. Log the detail server-side
against the same `request_id`.

**Validation errors should name every failing field at once**, not the first. A
client that has to submit five times to discover five problems is a form that
users abandon.

## Writes need to survive being retried

Networks retry. Load balancers retry. Mobile clients retry when the screen turns
off. A POST that charges a card or sends an email must be safe to receive twice.

Three mechanisms, in order of preference:

1. **A natural uniqueness constraint.** `UNIQUE (order_id, kind)` makes the second
   insert fail at the database, which is the only place that can decide it
   atomically.
2. **A client-supplied idempotency key**, stored with the result. Same key returns
   the stored response rather than doing the work again — including the same
   status code.
3. **A state machine with a guarded transition.** `UPDATE … SET status='charged'
   WHERE id=? AND status='pending'` and act only if it changed one row.

Checking "does it exist already?" and then inserting is not idempotency; it is a
race with a wider window than the thing it replaced.

This is the failure class that appears only in production, because in
development nothing retries.

## Transactions and consistency

- **A transaction is a boundary, not a decoration.** Everything inside it must
  either all matter or all not. Two writes that must agree belong in one; two
  writes that are independent should not share one, because the second failing
  rolls back the first.
- **Never do IO inside a transaction.** An HTTP call inside an open transaction
  holds locks for the length of someone else's outage. Do the external call
  first, or record intent and do it after commit.
- **The dual-write problem.** "Write to the database, then publish an event" has
  no atomic version. If the publish fails you have committed state nobody was
  told about. The transactional outbox — insert the event in the same
  transaction, a separate process publishes it — is the standard answer, and it
  makes delivery at-least-once, which loops back to idempotency on the consumer.
- **Side effects after commit, not before.** Sending the email inside the
  transaction means sending it for a transaction that then rolls back.
- **Know your isolation level.** Read-committed, the common default, permits
  non-repeatable reads: the same `SELECT` twice in one transaction can return
  different rows. Check-then-act across two statements is unsafe unless you lock
  or use a constraint.

## Pagination, filtering and unbounded results

Every list endpoint gets a limit, whether or not the client sends one. A default
of "everything" works until one customer has 400,000 rows, and then it takes the
service down for everyone.

**Prefer cursor pagination to offset** for anything large or actively changing.
`OFFSET 100000` makes the database walk 100,000 rows to discard them, and rows
inserted between pages shift the window so the client silently skips or repeats
items. A cursor over a stable, indexed, unique sort key has neither problem.

Cap the page size server-side. `?limit=1000000` is a denial of service written by
your own API.

## Background work

Anything slow — email, video processing, a third-party call, a report — should
not be holding a request open. Return 202 with a way to check status, and do the
work elsewhere.

If it must be inline, it needs a timeout. An external call with no timeout is an
outage waiting for the other side to hang, and it fails by exhausting your
connection pool rather than by erroring — so the symptom is "the whole service is
slow", not "that one endpoint is broken".

For the queue itself: assume **at-least-once** delivery and make handlers
idempotent; give every job a maximum attempt count and a dead-letter destination,
because a poison message retried forever is an infinite loop with a bill; and
make jobs small enough to retry cheaply. A job that is one thirty-minute step
loses thirty minutes to a transient failure.

## Caching

A cache is a second copy of the truth, and every problem with caching is that
copy disagreeing with the original.

- **The key must contain everything the value depends on** — tenant, user,
  locale, permissions, API version. A missing dimension serves one customer's
  data to another, and it happens under load rather than in tests.
- **Decide the invalidation before you add the cache.** "We'll figure it out" is
  how a stale price reaches checkout.
- **A short TTL is usually better than clever invalidation** and always better
  than no bound.
- **Cache stampede:** an expired hot key sends every concurrent request to the
  database at once. Serve stale while one caller refreshes.

## Configuration

Config comes from the environment, is read **once at startup**, and is validated
there — a missing variable should stop the process at boot with a clear message,
not fail the first request that happens to need it at 2am.

Never read `process.env` deep inside a handler; it makes the dependency invisible
and untestable. No secrets in defaults. Different environments differ only in
values, never in code paths — `if (env === 'production')` around behaviour means
you are testing something you do not ship.

## Observability

A handler that is impossible to diagnose in production is unfinished. The
minimum:

- **Structured logs**, one event per request, with the `request_id`, the route,
  the status, the duration and the tenant. Not a sentence containing them.
- **A correlation id that propagates** to every downstream call and every log
  line, so one identifier reconstructs the whole path.
- **Latency as a distribution, not a mean.** The mean hides the p99, and the p99
  is what your users experience as "it's broken".
- **Errors distinguished by cause.** A 4xx rate and a 5xx rate are different
  signals: one is clients being wrong, one is you being wrong.

Log the decision, not just the outcome — "rejected: balance 400 < required 500"
is answerable; "payment failed" starts an investigation.

## Before adding an endpoint

Read a neighbouring handler and match its shape for validation, error format,
pagination and response envelope. An endpoint that returns a different error
format from every other endpoint makes every client special-case it, forever.

Then answer four questions before writing it: who may call it, what may they
reach through it, what happens if they call it a thousand times, and what does it
return to someone who is nearly authorised.

## Review checklist

1. Is every input validated at the boundary, with bounds on size and count?
2. Does any handler take identity, price, role or tenant from the request body?
3. Does every resource fetch carry the ownership predicate?
4. Do status codes match the situation, and is there a 200-with-an-error?
5. Is the error body the same shape as the rest of the service, with a stable code?
6. Can this write be received twice safely? By which of the three mechanisms?
7. Is there IO inside a transaction, or a side effect before commit?
8. Is there a database write plus an event publish with no outbox?
9. Does every list endpoint have a server-side cap? Offset paging over big data?
10. Does every outbound call have a timeout?
11. Does a new cache key contain the tenant and every dimension the value varies on?
12. Is config read once at startup and validated there?
13. Can you reconstruct one request's path from logs using a single id?
