> TypeScript judgement — what the type system is being asked to prove, narrowing,
> generics, boundaries, async, module structure, and the escapes that quietly
> turn checking off. Loads automatically on .ts files and tsconfig.
>
> Domain: TypeScript

# TypeScript lens

The language, wherever it is written — including `.tsx`, which is where most
application TypeScript actually lives. React and Next.js *framework* judgement
belongs to `lens-frontend`; the type system is this page's, and the two load
together.

The thing to keep in view throughout: **TypeScript's types are erased at
runtime.** Every guarantee is a compile-time claim about code the compiler could
see. Where a value came from outside — the network, the database, `JSON.parse`,
`process.env`, a `localStorage` read — the type is a statement of intent, not a
fact, and the only thing that makes it true is a check you wrote.

## Types earn their place by making a bug impossible

A type that restates the shape of the code buys nothing. A type that rules out a
state the program should never reach buys a whole class of bug.

**Make illegal states unrepresentable.**

```ts
// Admits eight combinations. Two are contradictions, and every consumer has to
// handle a case that cannot happen but might.
type State = { loading: boolean; data?: User; error?: Error }

// Admits exactly three, and the compiler now enforces the handling.
type State =
  | { status: 'loading' }
  | { status: 'ok'; data: User }
  | { status: 'error'; error: Error }
```

The tell that you want this: two optional fields only ever set together, or a
boolean plus a value that is meaningless when the boolean is false.

**Exhaustiveness that fails loudly when a variant is added:**

```ts
function render(s: State) {
  switch (s.status) {
    case 'loading': return <Spinner />
    case 'ok':      return <Profile user={s.data} />
    case 'error':   return <Err e={s.error} />
    default: {
      const _exhaustive: never = s   // adding a variant breaks the build here
      throw new Error(`unhandled: ${JSON.stringify(_exhaustive)}`)
    }
  }
}
```

Without the `never` line, adding a fourth variant silently returns `undefined` at
runtime in every switch you forgot to update.

**Branded types for values that share a representation:**

```ts
type UserId = string & { readonly __brand: 'UserId' }
type OrgId  = string & { readonly __brand: 'OrgId' }
```

`getUser(orgId)` becomes a compile error rather than an empty result at 3am.
Worth it wherever two ids, two currencies, or two units of time flow through the
same functions.

## The escapes turn checking off silently

| Escape | What it actually does |
|---|---|
| `any` | Disables checking for everything it touches, including callers downstream |
| `as` | Tells the compiler to stop arguing. Does not make the value that shape |
| `as unknown as T` | A double override; nothing is being checked at all |
| `!` | "Trust me" at exactly the place people are usually wrong |
| `@ts-ignore` | Hides the next error too, including a new one |
| `?.` on a non-optional | Silences a symptom of a wrong type |
| Spread into a typed target | Excess-property checking does not apply |

`unknown` is almost always what `any` meant: it forces a narrowing before use.
`@ts-expect-error` is almost always what `@ts-ignore` meant: it fails when the
error goes away, so it cannot outlive the problem.

An `as` is sometimes correct — you know something the compiler cannot. When it
is, write the comment saying who guarantees it and what would break the
guarantee. An `as` with no comment is a claim nobody can check.

**The most dangerous cast is the honest-looking one:**

```ts
const user = await res.json() as User    // proves nothing about the response
```

## Narrow at the boundary, not everywhere after it

Data from the network, the database, `JSON.parse`, `process.env` or a form is
`unknown` in fact, whatever it is declared as. Validate once, where it enters,
and give the rest of the program a type it can rely on:

```ts
const User = z.object({ id: z.string(), email: z.string().email(), age: z.number().int() })
type User = z.infer<typeof User>

const user = User.parse(await res.json())   // throws here, near the cause
```

A cast at the boundary moves the lie inward, where the failure surfaces far from
its origin — usually as `Cannot read properties of undefined` three modules away,
with nothing to indicate that the API changed.

For cases where a schema library is too heavy, a type guard:

```ts
function isUser(v: unknown): v is User {
  return typeof v === 'object' && v !== null
    && typeof (v as Record<string, unknown>).id === 'string'
}
```

A `v is User` predicate is itself an unchecked assertion — the compiler takes
your word for the body. Keep them small enough to read in one go.

## tsconfig is where most of this holds or fails

`strict` is not a style preference; it is what makes the type system
load-bearing. Under it:

- `strictNullChecks` is why `user.name` on a possibly-missing user is an error.
  Turning it off to make an error go away removes the check that found the bug.
- `noImplicitAny` is why an untyped parameter is caught rather than inferred as
  `any` and spread outward from there.
- `strictFunctionTypes` catches unsound parameter variance in callbacks.

Two more worth enabling deliberately:

- **`noUncheckedIndexedAccess`** — `arr[0]` becomes `T | undefined`, which is the
  truth. Noisy at first, and it finds real bugs around `.find()`, `Record`
  lookups and split results.
- **`exactOptionalPropertyTypes`** — distinguishes "absent" from "present and
  `undefined`", which starts to matter the moment the object is serialised or
  written to a database.

## Nullability and the two empties

`null` and `undefined` are different, and the difference bites at every boundary:
JSON has `null` and cannot express `undefined`; `JSON.stringify` drops
`undefined` keys entirely; a database `NULL` arrives as `null`; a missing
property reads as `undefined`.

Pick one to mean "absent" in your own code and be consistent. Use `??` and `?.`
rather than `||` and truthiness, because `0`, `''` and `false` are valid values
that `||` throws away:

```ts
const perPage = opts.perPage || 20     // 0 silently becomes 20
const perPage = opts.perPage ?? 20     // 0 stays 0
```

## Generics relate types; they do not decorate them

A generic with one call site and no relationship between its parameters is a
concrete type wearing a costume. Reach for one when the **output** type depends
on the **input** type:

```ts
// Real: the return type is determined by the argument.
function first<T>(xs: readonly T[]): T | undefined

// Not real: T is used once and constrains nothing. Take `unknown`.
function log<T>(x: T): void
```

If you cannot state the relationship in a sentence, it is not a generic.

Use `extends` for constraints and `keyof`/`typeof` to derive rather than
duplicate:

```ts
function pick<T, K extends keyof T>(obj: T, keys: readonly K[]): Pick<T, K>
```

Conditional and mapped types are powerful and expensive — they slow the compiler
and their error messages are close to unreadable. A type that takes ten minutes
to understand costs those ten minutes to every future reader. Prefer three
explicit types over one clever one, unless the clever one removes a real class of
bug.

## Async

- **A floating promise is a lost error.** `void fn()` where you mean it, `await`
  otherwise. The `no-floating-promises` lint rule catches these and earns its
  noise.
- **`Promise.all` rejects on the first failure** and abandons the rest.
  `Promise.allSettled` when you need every result, failures included.
- **Sequential `await` in a loop** is N round trips. If the iterations are
  independent, map into `Promise.all`. If they are not, the sequence is correct
  and worth a comment saying so.
- **`async` inside `forEach` does nothing.** `forEach` ignores the returned
  promise, so the loop finishes before the work does. Use `for…of` with `await`,
  or map into `Promise.all`.
- **Errors in async callbacks** passed to non-async APIs vanish. An
  `EventEmitter` handler that throws asynchronously is an unhandled rejection.
- **`try/finally` for cleanup** — an `await` that throws skips everything after it.

## Modules and exports

Export what callers need and nothing else; every export is a promise you have to
keep. Prefer named exports — a default export is renamed at each import site, so
the same thing acquires several names and grep stops finding it.

Keep types next to the functions that use them rather than in a `types.ts` that
everything imports and nobody owns. That file becomes a cycle magnet and a
merge-conflict hotspot.

`import type { … }` makes the erasure explicit and stops a type-only import from
dragging a module's side effects into the bundle.

Watch for circular imports. TypeScript tolerates them at type level and they fail
at runtime as `undefined` during module initialisation — a symptom that looks
nothing like its cause.

## Enums, unions and `const`

Prefer a union of string literals to a TypeScript `enum`. Enums emit runtime
code, `const enum` breaks under `isolatedModules`, and numeric enums accept any
number:

```ts
enum Role { Admin, User }
const r: Role = 7        // no error

type Role = 'admin' | 'user'
const r: Role = 'x'      // error
```

`as const` turns a literal into its narrowest type and freezes it, which is what
makes derived unions work:

```ts
const ROLES = ['admin', 'user'] as const
type Role = typeof ROLES[number]     // 'admin' | 'user'
```

Now the list and the type cannot drift apart.

## Equality, comparison and the runtime underneath

The types do not save you from JavaScript:

- `===` always, except `== null`, which usefully catches both nullish values.
- `Array.prototype.sort` is lexicographic by default: `[10, 9].sort()` is
  `[10, 9]`. Always pass a comparator for numbers.
- `NaN !== NaN`; use `Number.isNaN`, not the global `isNaN`.
- Objects and arrays compare by reference, so a `Set` of objects deduplicates
  nothing.
- `structuredClone` for a deep copy. The spread is one level deep, and a nested
  object stays shared with the original.
- Floating point: `0.1 + 0.2 !== 0.3`. Money is integers of the smallest unit.

## Before adding a type

Look for one that already exists. Duplicated near-identical interfaces drift
apart, and the two then disagree about the same data — usually noticed when one
side is updated and the other is not. Derive with `Pick`, `Omit`, `Partial` and
`ReturnType` rather than restating, so a change to the source propagates on its
own.

## Review checklist

1. Does any type here make a bug impossible, or does it restate the code?
2. Two optional fields only ever set together — should this be a union?
3. Does every `switch` over a union have a `never` exhaustiveness check?
4. Any `any`, `as`, `!` or `@ts-ignore` — and does it carry a reason?
5. Is external data validated at the boundary, or cast and hoped for?
6. Is `strict` on, and did this change quietly relax a compiler option?
7. `||` where `??` was meant, on a value where `0` or `''` is legal?
8. A generic whose parameter is used once and relates nothing?
9. Any floating promise, `async` inside `forEach`, or `await` in a loop that
   could be `Promise.all`?
10. Default exports, a growing `types.ts`, or a new import cycle?
11. A numeric enum where a string-literal union would do?
12. A duplicated interface that should have been derived from the original?
