---
name: lens-database
description: Schema, migration and query judgement — Drizzle and SQL, transactions, indexes, N+1 access patterns and data integrity. Loads automatically when working in db/, drizzle/, migrations/ or on .sql files.
paths:
  - "db/**"
  - "drizzle/**"
  - "migrations/**"
  - "**/*.sql"
  - "**/schema.ts"
  - "**/drizzle.config.ts"
user-invocable: false
---

# Database lens

## Migrations are the one thing you cannot take back

Code can be reverted; a migration that has run in production has already changed
data. Treat every migration as permanent and one-way.

- Generate migrations with the project's own tooling (`drizzle-kit generate`),
  never by hand-editing a file that has already been applied.
- Adding a `NOT NULL` column to a populated table needs a default or a backfill,
  or it fails on real data while passing on an empty dev database.
- Renaming and dropping destroy data. Deploying code that stops using a column
  and dropping it later is two safe steps in place of one unsafe one.

## Let the database enforce what must be true

A uniqueness rule enforced only in application code is a rule that a second
concurrent request can break. Foreign keys, `unique`, `not null` and check
constraints are enforced under concurrency; a `SELECT` followed by an `INSERT`
is not.

Anything that must hold across several statements belongs in one transaction.
Two writes that must both happen, and currently do not share a transaction, is a
bug that only shows up under load or during a crash.

## Query shape

Loading a list and then querying once per row is the N+1 pattern, and it is the
most common cause of an endpoint that is fine in development and slow in
production. Use a join or a single `where in`.

Anything that appears in a `where` or `order by` on a table that will grow wants
an index. Anything that selects columns nobody reads is wasted transfer —
`select *` in application code is rarely deliberate.

## Before changing the schema

Read the existing schema first and follow its conventions for naming, timestamps
and soft deletes. Check whether a column or table already carries this
information under a different name. A near-duplicate table is far more expensive
than a near-duplicate function: the data diverges, and then both are wrong.
