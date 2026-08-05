> Schema, migration and query judgement — modelling, constraints, safe online
> migrations, indexes, transactions and isolation, N+1 access patterns, JSON
> columns, soft deletes and query performance. Loads automatically when working
> in db/, drizzle/, migrations/ or on .sql files.
>
> Domain: schema and queries

# Database lens

This loads on `db/`, `migrations/` and every `.sql` file, whatever the project
actually uses — Drizzle, Prisma, Alembic, Rails, or hand-written SQL. Where a
specific tool is named below it is an example; check what this repo declares
before repeating it. The judgement holds everywhere, the commands do not.

The premise for the whole page: **the schema outlives the code.** Application
code gets rewritten every couple of years; the data survives every rewrite, and
every wrong decision in the schema is inherited by whatever comes next. It is the
most expensive place in the system to be wrong and the cheapest place to be
right, because a constraint written once is enforced on every path forever,
including the paths nobody has written yet.

## Let the database enforce what must be true

A rule enforced only in application code is a rule a second concurrent request
can break — and a rule the next service, the next migration script and the next
manual fix in a psql session will not know about.

```sql
-- Enforced under concurrency, by the only component that can decide atomically.
UNIQUE (org_id, email)
FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE RESTRICT
CHECK (quantity > 0)
CHECK (ends_at > starts_at)
NOT NULL
```

`SELECT` then `INSERT` is not uniqueness; it is a race with a window. Two
requests both find nothing and both insert. It passes every test, because tests
run one at a time.

**`NOT NULL` deserves its own line.** A nullable column is a third state every
future reader has to handle, and `NULL` propagates in ways people do not expect:
`NULL = NULL` is not true, `NOT IN (…, NULL)` returns nothing at all, and
aggregates skip nulls silently. Make a column nullable only when "absent" is a
real, distinct, documented state — not because you did not have a value at insert
time.

**Foreign keys deserve a decision, not a default.** `ON DELETE CASCADE` on a
table with financial records deletes financial records. `RESTRICT` is the safe
default; cascade only where the child genuinely has no meaning without the
parent.

## Types: pick the one that cannot represent a wrong value

| Data | Use | Never |
|---|---|---|
| Money | Integer minor units, or `NUMERIC` | `float`/`double` — 0.1+0.2 |
| Timestamps | `timestamptz` | `timestamp` without a zone |
| Enumerated states | A constrained text column or a lookup table | Free text |
| Identifiers | `uuid`, or `bigint` — not `int` | `int` that will hit 2.1 billion |
| Booleans that grew a third state | Make it the state column it became | `NULL` as the third value |

`timestamptz` is worth insisting on. A `timestamp` without a zone is a number
whose meaning depends on the server's configuration, and it changes meaning when
the server moves. Store UTC, convert at the edge, and store the user's zone
separately when you need to render or schedule in local time — "9am every
Tuesday" is not a UTC offset, because of daylight saving.

## Migrations are the one thing you cannot take back

Code can be reverted. A migration that has run in production has already changed
data. Treat every migration as permanent and one-way.

- Generate migrations with the project's own tooling — `drizzle-kit generate`,
  `prisma migrate dev`, `alembic revision` — never by hand-editing a file that has
  already been applied. Editing an applied migration means the next environment
  gets a different schema from the last one, and nothing detects it.
- **Adding a `NOT NULL` column to a populated table** needs a default or a
  backfill. It passes on an empty dev database and fails on real data.
- **Renaming and dropping destroy data.** Deploying code that stops using a
  column, then dropping it a release later, is two safe steps instead of one
  unsafe one.
- **A migration must be safe to run against the previous release's code**, which
  is still running during the deploy. This is why the two-step exists.

### Expand / contract

The pattern that makes every schema change safe, at the cost of three deploys:

1. **Expand.** Add the new column, nullable, with a default. Old code ignores it.
2. **Migrate.** Deploy code that writes both old and new, backfill the existing
   rows in batches, then deploy code that reads the new one.
3. **Contract.** Only once nothing reads the old column: drop it.

Skipping step 2 is what causes the "it worked in staging" deploy failure, because
staging did not have a previous version running at the same time.

### The locks that take a site down

The dangerous part of a migration is usually not the data change, it is the lock.
On Postgres:

- `ALTER TABLE … ADD COLUMN` with a **volatile** default rewrites the table.
  A constant default does not, on modern versions — check yours.
- `CREATE INDEX` locks writes for the duration. `CREATE INDEX CONCURRENTLY` does
  not, cannot run inside a transaction, and can leave an invalid index behind if
  it fails — which you then have to drop and retry.
- `ALTER TABLE … ADD CONSTRAINT … CHECK` scans the whole table under a lock. Add
  it `NOT VALID`, then `VALIDATE CONSTRAINT` separately.
- **Any DDL waits for a lock, and everything behind it queues.** A migration that
  waits ten seconds behind one long-running `SELECT` stalls every write to that
  table for those ten seconds. Set a `lock_timeout` so the migration fails
  instead of taking the site with it.

### Backfills

Never `UPDATE` ten million rows in one statement. It holds a transaction open for
minutes, bloats the table, and blocks vacuum. Batch it — a few thousand rows at a
time, with a bound and a sleep — and make the batch resumable, because it will be
interrupted.

A migration that can stop halfway must leave the system in a state that works.
Ask directly: if this dies at 40%, is the application still correct?

## Transactions and isolation

Anything that must hold across several statements belongs in one transaction. Two
writes that must both happen and currently do not share one is a bug that appears
under load or during a crash, and never in a test.

- **Know your isolation level.** Read-committed — the usual default — allows
  non-repeatable reads: the same `SELECT` twice in one transaction can return
  different rows. Any check-then-act across two statements is unsafe under it.
- **`SELECT … FOR UPDATE`** takes the row lock that makes check-then-act correct.
  It also serialises everything behind it, so keep the transaction short.
- **Take locks in a consistent order** everywhere in the codebase. Two
  transactions locking A then B, and B then A, deadlock. The database will kill
  one of them; your code needs to be ready to retry.
- **Never do IO inside a transaction.** An HTTP call inside an open transaction
  holds locks for the length of someone else's outage.
- **Long-running transactions block vacuum**, and a table that cannot be vacuumed
  grows without bound. The idle-in-transaction connection is the classic cause.

## Indexes

Anything in a `WHERE`, `JOIN`, `ORDER BY` or foreign key on a table that will
grow wants an index. Beyond that:

- **Column order in a composite index matters.** `(org_id, created_at)` serves
  `WHERE org_id = ?` and `WHERE org_id = ? ORDER BY created_at`. It does not
  serve `WHERE created_at > ?` alone. Equality columns first, then the range or
  sort column.
- **A function on the indexed column disables the index.** `WHERE lower(email) =
  ?` needs an index on `lower(email)`. Same for `WHERE date(created_at) = ?` and
  for an implicit cast between a `text` column and a number.
- **A leading wildcard cannot use a B-tree.** `LIKE '%foo'` scans. Use a trigram
  index or full-text search.
- **Foreign keys are not indexed automatically** in Postgres or MySQL/InnoDB on
  the child side. A `DELETE` on the parent then scans the child table.
- **Every index costs writes and space.** An index nothing uses is pure overhead;
  check `pg_stat_user_indexes` before adding the fourth one to a hot table.
- **Partial indexes** for the common filtered case — `WHERE deleted_at IS NULL` —
  are small and fast.
- **Read the plan, do not guess.** `EXPLAIN (ANALYZE, BUFFERS)` on real-sized
  data. A sequential scan on 200 rows is correct; the planner is usually right
  and the query is usually the problem.

## Query shape

**N+1** — load a list, then query once per row — is the most common cause of an
endpoint that is fine in development and slow in production, because development
has twelve rows. Use a join or a single `WHERE id IN (…)`. With an ORM, this is
usually a missing `include`/`joinedload`/`with`, and it is invisible in the code:
the loop looks like it is reading a field.

Others worth naming:

- **`SELECT *` in application code** transfers columns nobody reads, defeats
  covering indexes, and breaks in a new way when someone adds a `TEXT` column.
- **Unbounded queries.** Every query against a growing table needs a `LIMIT`,
  including the ones in scripts and reports.
- **`OFFSET` deep in a large table** makes the database walk and discard every
  skipped row. Cursor pagination on an indexed, unique, stable sort key instead.
- **`COUNT(*)` on a large table is a full scan** in Postgres. An approximate
  count from the statistics, or a maintained counter, is usually what the page
  actually needed.
- **`IN` with ten thousand ids** stops being a query and becomes a parser
  problem. Batch it, or use a temporary table.
- **Aggregation in application code** — pulling 50,000 rows to sum a column — is
  transferring the whole table to avoid learning `GROUP BY`.

## JSON columns

A JSON column is the right answer for genuinely unstructured data — a third-party
webhook payload you store verbatim, user-defined form fields — and the wrong
answer for a field you know the name of. Inside JSON there are no constraints, no
foreign keys, no type checking and no cheap index. Fields that graduate into
being queried should graduate into being columns.

The tell that it went wrong: application code that reads one JSON key on every
request, or a `WHERE data->>'status' = 'active'` with no index.

## Soft deletes

`deleted_at` is a decision with consequences, not a free safety net. Once you
have one:

- Every query needs `WHERE deleted_at IS NULL`, and the one that forgets is a
  bug that shows deleted data to a user. Enforce it in one place — a view, a
  default scope, or row-level security — not by convention.
- **Unique constraints stop working.** `UNIQUE (email)` blocks re-registering an
  email that belongs to a deleted row. A partial unique index `WHERE deleted_at
  IS NULL` is the fix.
- Foreign keys still point at soft-deleted rows, so "delete" is invisible to the
  database's own integrity rules.

Decide deliberately: soft delete, hard delete plus an audit table, or an archive
table. All three are defensible; drifting into soft deletes because it seemed
safer is not.

## Multi-tenancy

If rows from two customers share a table, the tenant id belongs in every query
and in every unique constraint, enforced somewhere structural — row-level
security, a session variable, or a base query object nothing bypasses. A
convention that everyone remembers to filter fails on the first raw query written
for a report.

Also put it in the index. `UNIQUE (email)` in a multi-tenant table is almost
always meant to be `UNIQUE (tenant_id, email)`.

## Before changing the schema

Read the existing schema and follow its conventions for naming, timestamps,
primary keys and soft deletes. Then check whether a column or table already
carries this information under a different name.

A near-duplicate table is far more expensive than a near-duplicate function: the
data diverges, both become partly wrong, and no refactor fixes it afterwards
because you no longer know which row was right.

## Review checklist

1. Is every invariant enforced by a constraint, or only by application code?
2. Is any new column nullable without "absent" being a real, distinct state?
3. Money in floats? Timestamps without a zone? An `int` id that will overflow?
4. Is this migration safe to run while the previous release is still serving?
5. Does it take a lock that blocks writes — index, check constraint, table rewrite?
6. Does a backfill run in batches, and is it correct if it stops at 40%?
7. Is there a check-then-act across two statements without a lock or a constraint?
8. Any IO inside a transaction? Any transaction held open across a slow call?
9. Does a new query have an index that its column order can actually serve?
10. A function or implicit cast on an indexed column in the `WHERE`?
11. Any loop that queries — N+1? Any query without a `LIMIT`?
12. Deep `OFFSET`, `COUNT(*)` on a big table, or aggregation done in the app?
13. Is a queried field living inside a JSON column?
14. With soft deletes: is the filter structural, and are unique indexes partial?
15. In a shared table, is `tenant_id` in the query *and* in the unique constraint?
