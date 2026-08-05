> Failure judgement — what happens when a dependency is slow, a write lands
> twice, a deploy goes out half-broken, or the thing that was down comes back.
> Load it on retries, queues, jobs, external calls and anything with a timeout.
>
> Domain: failure modes, resilience and operations

# Resilience lens

Code that works is the easy half. This lens is about what the code does when
something it depends on does not work — which, at any scale, is always.

The governing distinction:

> **A crash is a good failure. A wrong answer is a bad one.**

A process that dies gets restarted, alerted on, and noticed. A process that
silently returns stale, partial or duplicated data is discovered weeks later by a
customer. Most of the advice below trades the second for the first.

## Timeouts: the default is infinity, and infinity is an outage

Every call that leaves the process needs a timeout. Every one. The default in
most HTTP clients, database drivers and queue libraries is no timeout at all,
which means a dependency that stops responding — without closing the connection —
holds your worker forever.

**How the outage actually happens**, because it is never the slow dependency
itself:

```
downstream service gets slow (not down — slow)
  → your requests to it pile up, each holding a worker
  → your worker pool exhausts
  → requests that never touch that service start timing out
  → you are down, for a reason unrelated to what broke
```

This is why a slow dependency is more dangerous than a dead one. A dead one fails
fast.

- **Set a timeout on every outbound call**, including DNS, connection, and read
  separately if the client allows it.
- **The timeout must be shorter than the caller's timeout.** If your API has a
  30s budget and you call something with a 60s timeout, you have guaranteed the
  caller gives up first and your work is wasted.
- **Budget across a chain, do not repeat it.** A → B → C each with 10s is 30s.
  Pass the remaining budget down.
- **A timeout is not a failure.** The other side may have completed the work. See
  idempotency below — this is the single most common source of duplicates.

**Tell in review**: `fetch(`, `axios.`, `requests.`, `http.Get`, `.query(` with
no timeout argument anywhere on the path.

## Retries: which failures, and why retrying is dangerous

Retrying is not free and it is not always safe.

    the operation is idempotent AND the failure is transient  → retry
    the operation is not idempotent                           → make it idempotent first
    the failure is deterministic (400, 422, parse error)      → NEVER retry. It will fail identically
    you do not know whether it completed (timeout, 502)       → retry ONLY if idempotent

| Retry | Do not retry |
|---|---|
| Connection refused, reset, DNS failure | 400, 401, 403, 404, 422 |
| 429, 502, 503, 504 | Any validation or parse failure |
| Deadlock, serialisation failure | Anything that failed the same way twice |
| Read timeout on a read | Write timeout on a non-idempotent write |

**Three rules that make retries safe:**

1. **Exponential backoff with jitter.** Without backoff you turn a blip into a
   flood. Without *jitter* every client retries in lockstep — the thundering herd
   that keeps the recovering service down.

```python
delay = min(base * 2 ** attempt, cap)
time.sleep(random.uniform(0, delay))   # full jitter — the randomness is the point
```

2. **A hard attempt cap and a total time budget.** Both. Five attempts with
   backoff can exceed the caller's patience long before the fifth.

3. **Retry at exactly one layer.** Retries at three layers multiply: 3 × 3 × 3 is
   27 requests for one logical call. This is a real and common outage cause. Pick
   the layer closest to the failure that knows whether the operation is safe to
   repeat, and make every other layer pass the failure up.

**Tell**: a retry wrapper around a function that itself retries; a retry loop
with no `sleep`; a retry on a `POST` with no idempotency key.

## Idempotency: doing it twice must equal doing it once

The property that makes retries, queues and at-least-once delivery survivable.
Without it, every timeout is a coin flip on whether a customer gets charged
twice.

```python
# Not idempotent — retry double-charges.
def charge(user_id, amount):
    stripe.charge(user_id, amount)
    db.insert_payment(user_id, amount)

# Idempotent — the key makes the second call a no-op that returns the first result.
def charge(user_id, amount, idempotency_key):
    existing = db.find_payment(idempotency_key)
    if existing:
        return existing
    result = stripe.charge(user_id, amount, idempotency_key=idempotency_key)
    return db.insert_payment(idempotency_key, user_id, amount, result.id)
```

- **The key comes from the caller**, generated once before the first attempt, and
  reused across every retry of that logical operation. A key generated inside the
  retry loop is a new key each time and buys nothing.
- **The uniqueness must be enforced by the database**, not by a check-then-insert
  in application code. Two concurrent retries both pass the check.
  `UNIQUE (idempotency_key)` and catching the violation is correct; `if not
  exists: insert` is a race.
- **Naturally idempotent beats bolted-on.** `SET status = 'paid'` is idempotent.
  `balance = balance - 10` is not. Prefer the first shape when you can choose.

**Tell**: any external write inside a retry, any handler for an at-least-once
queue, any webhook receiver — all three must be idempotent and usually are not.

## Circuit breakers: stop calling what is already failing

Retrying against a service that is down makes its recovery slower and burns your
capacity. A breaker notices the failure rate and stops trying.

```
CLOSED   normal. Count failures.
   │     failure rate over threshold →
   ▼
OPEN     fail immediately without calling. No load on the dependency.
   │     after a cooldown →
   ▼
HALF-OPEN  let ONE request through.
           succeeds → CLOSED.  fails → OPEN, cooldown again.
```

The half-open state is the part usually implemented wrong: it must admit *one*
request, not resume full traffic, or recovery immediately re-floods the service.

Worth it for a dependency that is: called often, has a fallback, and whose
slowness would exhaust your workers. Not worth it for a single call in a batch
job.

**The fallback matters more than the breaker.** An open breaker with no fallback
just fails faster. Decide per dependency: cached value, degraded response,
queue-for-later, or a clean error the caller can act on.

## Partial failure: the state nobody designed

Multi-step operations fail in the middle. The question is what the system looks
like afterwards.

```python
# Fails between the two → charged, no order. The customer's money is gone.
charge_card(order)
create_order(order)
```

In descending order of preference:

1. **Make it one atomic operation.** One transaction, if it is all one database.
   This solves it completely and is usually available.
2. **Order the steps so the failure is safe.** Create the order as `pending`
   first, then charge, then mark `paid`. A failure leaves a pending order, which
   is a state you can reason about and retry.
3. **Compensate.** If step 2 fails, undo step 1 — and know that the compensation
   can fail too, so it must be retryable and recorded.
4. **Reconcile.** A periodic job that finds inconsistent state and fixes it.
   Slowest to notice, but the only option across systems you do not control.

**Never** distribute a transaction across a database and an external API and hope.
`db.commit()` after `stripe.charge()` fails at exactly the wrong moment
eventually, and the amount of money involved is not zero.

**Tell**: two or more side effects in sequence with no transaction, no ordering
argument, and no compensation.

## Graceful degradation: decide what is load-bearing

For every dependency, decide in advance: **if this is down, does the feature fail
or does the page fail?**

```tsx
// The recommendations service being down takes down the product page.
const [product, recs] = await Promise.all([getProduct(id), getRecs(id)])

// It does not.
const product = await getProduct(id)                    // load-bearing: let it fail
const recs = await getRecs(id).catch(() => [])          // decorative: degrade
```

Write it down per dependency. The failure mode this prevents — a decorative
feature taking down a critical path — is extremely common and completely
avoidable, and it is invisible until the day it happens.

## Health checks that mean something

- **Liveness**: "is this process wedged?" Should check almost nothing. A liveness
  check that queries the database restarts every instance when the database
  blips, which is the opposite of helpful.
- **Readiness**: "should this instance receive traffic?" Checks the dependencies
  it genuinely cannot serve without. Fails during startup, and during shutdown
  *before* the process stops accepting connections.
- **Neither should be expensive.** They run every few seconds forever.

**Tell**: a `/health` endpoint that runs a database query and is used for
liveness; a readiness check that returns 200 before caches or connection pools
are warm.

## Shutdown, and the requests you drop by not thinking about it

```
SIGTERM received
  → fail readiness immediately (stop new traffic arriving)
  → keep serving in-flight requests
  → finish or requeue in-flight jobs
  → close connections and flush buffers
  → exit before the platform's SIGKILL deadline
```

Exiting immediately on SIGTERM drops every in-flight request, and every rolling
deploy becomes a small outage. Most platforms give 30 seconds; a job that takes
longer must be checkpointed or requeued, not abandoned.

**Tell**: no SIGTERM handler; a worker loop with no cancellation check; a job that
cannot be resumed from the middle.

## Queues and background work

- **At-least-once is the norm.** Exactly-once is mostly a marketing claim.
  Consumers must be idempotent. This is not optional.
- **Every queue needs a dead-letter queue**, and the DLQ needs an alert. A DLQ
  nobody watches is a directory where failures go to be forgotten.
- **Cap the retry count per message.** A poison message retried forever consumes
  the consumer, and the queue backs up behind one bad row.
- **Order is not guaranteed** unless you have specifically bought it. Design for
  messages arriving out of order, or key them so ordering only matters within a
  key.
- **Watch queue depth, not just error rate.** A consumer that is merely slower
  than the producer looks healthy right up until it is hours behind.

## Rate limits and backpressure

- **Respect `Retry-After`.** It is the server telling you exactly what to do.
  Ignoring it and using your own backoff is how you stay rate-limited.
- **Under overload, shed load early.** Rejecting 10% of requests fast is better
  than accepting all of them and timing out on 100%. A full queue should reject,
  not grow.
- **Bound every queue, buffer and pool.** An unbounded queue is a memory leak
  with a scheduled OOM.

## Observability at the moment of failure

Failure handling you cannot see is failure handling you cannot trust.

- **Log the retry, not just the final failure.** "Succeeded after 4 attempts" is
  the early warning; without it, the first sign is the outage.
- **Log the cause, with context.** `except Exception: pass` is the single most
  expensive line in this document. If you genuinely must swallow it, log it at
  warning with the exception and enough identifiers to find the record.
- **Never log secrets, tokens or full request bodies.** Log an identifier and
  look it up.
- **Alert on the symptom users feel** (error rate, latency, queue depth), not on
  causes (CPU). Cause alerts fire when nothing is wrong and stay silent when
  something is.

## Review checklist

1. Does every outbound call have a timeout, and is it shorter than the caller's?
2. Is anything retried that is not idempotent?
3. Is there backoff, jitter, an attempt cap, and retries at exactly one layer?
4. Are non-retryable failures (4xx, parse errors) excluded from the retry?
5. What does a failure between step 1 and step 2 leave behind?
6. Is any decorative dependency able to fail the whole request?
7. Is there an unbounded queue, buffer, pool or result set?
8. Does SIGTERM drain, or drop?
9. Is any exception swallowed without a log?
10. If this is retried by the queue, does the customer get charged twice?
