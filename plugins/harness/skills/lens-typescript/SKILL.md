---
name: lens-typescript
description: TypeScript judgement — what the type system is being asked to prove, narrowing, generics, module boundaries and the escapes that quietly turn checking off. Loads automatically on .ts files and tsconfig.
paths:
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.mts"
  - "**/*.cts"
  - "tsconfig.json"
user-invocable: false
---

# TypeScript lens

The language, wherever it is written — including `.tsx`, which is where most
application TypeScript actually lives. React and Next.js *framework* judgement is
`lens-frontend`'s; the type system is this one's, and the two load together.

## Types earn their place by making a bug impossible

A type that restates the shape of the code buys nothing. A type that rules out a
state the program should never reach buys a whole class of bug.

- **Make illegal states unrepresentable.** `{ status: 'loading' } | { status:
  'ok'; data: T } | { status: 'error'; error: E }` is better than
  `{ loading: boolean; data?: T; error?: E }`, which admits four combinations
  that mean nothing and one that means two things at once.
- **Prefer a discriminated union to an optional flag** whenever two fields are
  only ever set together.

## The escapes turn checking off silently

- **`any` disables checking for everything it touches**, including callers
  downstream. `unknown` is almost always what was meant: it forces a narrowing
  before use.
- **`as` is an assertion, not a conversion.** It tells the compiler to stop
  arguing; it does not make the value that shape. `as unknown as T` is a
  double-override and needs a comment saying who guarantees it.
- **Non-null `!`** says "trust me" at exactly the place people are usually wrong.
  Narrow instead.
- **`@ts-ignore` hides the next error too.** `@ts-expect-error` fails when the
  error goes away, which is what you want.

## Narrow at the boundary, not everywhere after it

Data from the network, the database, `JSON.parse` or `process.env` is `unknown`
in fact whatever it is declared as. Validate once, where it enters, and give the
rest of the program a type it can rely on. A cast at the boundary moves the lie
inward, where the failure surfaces far from its cause.

`strict` in `tsconfig.json` is what makes most of this hold. Turning off
`strictNullChecks` to make an error go away removes the check that finds the bug.

## Generics are for relating types, not for looking clever

A generic with one call site and no relationship between its parameters is a
concrete type wearing a costume. Reach for one when the *output* type depends on
the *input* type. If you cannot state that relationship in a sentence, it is not
a generic.

## Modules and exports

Export what callers need and nothing else — every export is a promise. Prefer
named exports; a default export is renamed at each import site, so the same
thing acquires several names. Keep types and their functions together rather
than in a `types.ts` that everything imports and nothing owns.

## Before adding a type

Look for one that already exists. Duplicated near-identical interfaces drift
apart, and the two then disagree about the same data — usually noticed when one
side is updated and the other is not.
