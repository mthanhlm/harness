> Contract judgement — what breaks callers you cannot see, how to change a shape
> that is already in use, and how to run a migration that can be stopped halfway.
> Load it on any change to a signature, a schema, a payload or a stored format.
>
> Domain: API contracts, versioning and migration

# Contracts lens

This is the lens for the question "what does quality mean in a repository that
keeps changing?" The answer is not that today's code is elegant. It is that a
change made in August does not break code written in June.

A contract is any shape something else depends on: a function signature, an HTTP
payload, a database column, a queue message, a file format, an environment
variable, an exit code, a log line something greps. **You do not own a contract
once something else reads it** — you own the migration.

The failure mode this lens exists for is silent and delayed. A breaking change
does not fail at the moment it is made. It fails later, in something you did not
edit, for someone who did not deploy.

## The one question

> **Who consumes this, and can they all be changed at the same instant?**

    everything is in this repo, in this deploy      → change it freely, fix the callers
    a separate deploy, another team, a mobile app,
    stored data, a webhook subscriber, a cron       → you cannot. Expand and contract

Almost every mistake here comes from answering the first when the truth is the
second. Stored data is the one people forget: **a row written last year is a
caller you cannot redeploy.**

## What actually breaks a caller

Adding is usually safe. Removing, renaming, narrowing and re-meaning are not.

| Change | Breaks | Why |
|---|---|---|
| Add an optional field | no | old readers ignore it |
| Add a required field | **yes** | old writers do not send it |
| Add a required parameter with no default | **yes** | every existing call site |
| Remove or rename a field | **yes** | readers looking for it get `undefined` |
| Widen a type (`string` → `string \| null`) | **yes for readers** | they did not handle null |
| Narrow a type (`string \| null` → `string`) | **yes for writers** | they still send null |
| Add an enum value | **yes for readers** | exhaustive switches fall through |
| Remove an enum value | **yes for writers** | stored rows still hold it |
| Loosen validation | no | previously-valid input stays valid |
| Tighten validation | **yes** | data already in flight now fails |
| Reorder positional arguments | **yes, silently** | same arity, wrong meaning, no type error |
| Change a default | **yes, silently** | callers relying on the old one change behaviour |
| Change units or precision | **yes, silently** | seconds→ms, cents→dollars. Nothing errors |
| Make an error into a success | **yes** | callers with error handling now take the wrong branch |
| Change ordering of results | **yes** | pagination and "the first one" both depend on it |
| Change a rate limit or timeout | **yes** | a slower caller starts failing |

**The three silent ones** — reordered arguments, changed defaults, changed units
— deserve special fear. Every other row on this table produces a type error or a
404 somewhere. Those three produce *wrong answers*, with a green build.

### Adding an enum value is the one people get wrong

It reads like addition, so it reads like it is safe. It is not: every reader with
an exhaustive `switch` or a `match` now has a case it does not handle.

```ts
// Was: 'pending' | 'active'
// Now: 'pending' | 'active' | 'suspended'

function label(s: Status) {
  switch (s) {
    case 'pending': return 'Waiting'
    case 'active':  return 'Live'
  }                              // returns undefined for 'suspended'
}
```

**Tell**: a union type or enum gained a member, and any reader lacks a `default`
or an exhaustiveness check. In TypeScript, `const _: never = s` in the default
branch turns this into a compile error — add it when you add the enum, not after.

## Expand and contract

The general shape for changing anything with callers you cannot redeploy. Three
deploys, and **the gap between them is measured in whatever the slowest consumer
is** — for a mobile app, months.

```
1. EXPAND    add the new thing beside the old one.
             Write BOTH. Read the old one. Deploy.
             ↓ (both work; nothing has broken)
2. MIGRATE   move readers to the new thing, one at a time.
             Backfill existing data. Deploy each.
             ↓ (verify: nothing reads the old one — logs, metrics, grep)
3. CONTRACT  stop writing the old one. Wait. Then remove it.
             ↓
```

**Every step is independently deployable and independently revertible.** That is
the property being bought, and it is the reason not to collapse the three into
one "clean" change.

Renaming a column, done properly:

```sql
-- 1. expand
ALTER TABLE users ADD COLUMN email_address text;          -- nullable, no default
-- application writes both columns, reads `email`

-- 2. migrate
UPDATE users SET email_address = email WHERE email_address IS NULL;  -- batched
-- application reads `email_address`, still writes both

-- 3. contract  (a separate deploy, after verifying nothing reads `email`)
ALTER TABLE users DROP COLUMN email;
```

**The tell that someone skipped it**: a migration that renames a column in one
statement. It is atomic in the database and not atomic in the fleet — during the
rollout, old application instances are still writing to a column that no longer
exists.

## Migrations that can be stopped halfway

Assume every migration will be interrupted: a deploy fails, a lock times out, a
row is bad, someone hits Ctrl-C.

- **Additive first, always.** Add nullable, backfill, then add the constraint.
  `ALTER TABLE ... ADD COLUMN x NOT NULL` on a populated table is either a
  failure or a full table lock; neither is what you wanted.
- **Backfill in batches, and make it resumable.** One `UPDATE` over ten million
  rows takes a lock nobody budgeted for. Loop over a key range, commit each
  batch, and let a second run skip what is already done.
- **Separate schema change from data change from code change.** Three deploys.
  When something goes wrong you can revert exactly one.
- **Never make a destructive change in the same deploy as the code that stops
  needing it.** If the code has to be rolled back, the data is gone.
- **Write the reverse.** If the down migration cannot be written, say so
  explicitly in the plan — that is a decision to accept, not a detail to omit.
- **Long-lived transactions are an outage.** A migration holding a lock while it
  does application work blocks writers behind it.

**Tell in review**: `NOT NULL` or `UNIQUE` added in the same statement as the
column; an `UPDATE` with no `WHERE` bound; a migration file that also changes
application code.

## Versioning, and when it is worth it

Versioning is expensive — every version is a code path that must keep working —
so do not reach for it first.

    consumers you can redeploy         → just change it
    a few consumers, slow to move      → expand/contract, no version
    public API, unknown consumers      → version
    every change is breaking something → the design is wrong, not the versioning

When you do version:

- **Version the contract, not the file.** `/v2/users` is a contract boundary;
  `users_v2.ts` is a naming convention that will be `users_v3_final.ts` inside a
  year.
- **Say when the old one dies, in writing, at the moment you ship the new one.**
  A deprecation with no date is a permanent maintenance cost.
- **Measure the old version before removing it.** "Nobody uses it" is a claim.
  Count the requests.
- **Deprecate loudly for the developer, silently for the user.** A log line, a
  response header, a compiler warning. Never a user-visible error.

## Semantic versioning, honestly

`MAJOR.MINOR.PATCH` — major for breaking, minor for additive, patch for fixes.
The rule is easy and the judgement is not:

- **A bug fix that something depends on is a breaking change.** If callers worked
  around the bug, fixing it breaks them. This is real, and it is the case the
  rule does not cover.
- **Anything observable is part of the contract, whether you meant it or not.**
  Error message text people match on, iteration order, timing, log format.
- **`0.x` means the contract is not stable yet.** Say so, and stop being `0.x`
  once people depend on it.

## Contracts nobody writes down

These are contracts. They break the same way. They have no schema file, so
nothing checks them.

- **Environment variables.** Renaming one is a breaking change to every
  deployment. Adding a required one breaks every environment that does not have
  it yet. **Fail loudly at startup on a missing required variable**, never
  silently default to something plausible.
- **Exit codes and CLI flags.** Scripts depend on both. Changing `-v` from
  verbose to version breaks quietly.
- **Log lines and metric names.** If an alert greps it, it is a contract. Renaming
  a metric silently disables the alert built on it, and nothing anywhere fails.
- **File and directory layout.** Anything that writes to a known path.
- **Queue message shapes.** Worse than HTTP: a message written by the new
  producer may be read by an old consumer *and vice versa*, and the queue may
  hold messages written before the deploy. Version the payload, and make
  consumers tolerate a shape they do not recognise rather than crash-looping.
- **Serialised state.** A cached object, a session blob, a job payload in Redis.
  Changing the class it deserialises into breaks everything already in flight.
  This is the one that fails at 3am during a rolling deploy.
- **Database column semantics.** Same column, same type, new meaning — the worst
  case in this document, because nothing can detect it.

## Backwards compatibility for readers

Be strict in what you emit, tolerant in what you accept.

```ts
// Fragile: any new field the producer adds crashes the consumer.
const { id, name } = strictSchema.parse(payload)      // throws on unknown keys

// Tolerant: unknown fields pass through, known ones are validated.
const { id, name } = schema.passthrough().parse(payload)
```

For anything crossing a deploy boundary, **an unrecognised value should degrade,
not throw**. An unknown enum member should render as "unknown", get logged, and
let the rest of the page work — not blank the screen.

The exception is security-relevant input, where unknown fields should be
rejected. Tolerance is for shapes you own on both ends; strictness is for input
from outside the trust boundary.

## Review checklist

For any diff that touches a signature, a schema, a payload or a stored format:

1. Who reads this? List them. Include stored data and old clients.
2. Can they all deploy at the same moment? If no → expand/contract.
3. Is anything removed, renamed, narrowed, reordered, or re-meaned?
4. Is a new field required? Does existing data satisfy it?
5. Was an enum value added? Is every reader exhaustive?
6. Did a default, unit or precision change? (No type error will catch it.)
7. Is the migration resumable, batched, and reversible?
8. If this is rolled back, is the data still readable?
9. Is anything deprecated without a removal date?
10. Did a log line, metric or env var change that something depends on?
