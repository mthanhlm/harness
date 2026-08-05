> React and Next.js judgement — server versus client, data fetching and cache
> semantics, state modelling, effects, forms, accessibility, render cost, and the
> failure modes that only appear on a real network. Loads automatically when
> working on .tsx/.jsx/.css files or in components/.
>
> Domain: web frontend

# Frontend lens

Framework judgement lives here; the type system is `lens-typescript`, and the two
load together on `.tsx`.

The frame for the whole page: **the frontend is a distributed system with an
unreliable node you do not control.** The network is slow, the user double-clicks,
the tab was backgrounded for an hour, the request that started first finishes
last, and the device has a quarter of your laptop's CPU. Almost every bug worth
finding here is one of those, and none of them reproduce on localhost.

## Server and client are a decision, not a default

In the App Router every component is a server component until `"use client"` says
otherwise. That directive is load-bearing and it spreads: it applies to
everything the file imports, transitively.

Before adding it, check whether only a leaf actually needs interactivity. Pushing
the boundary down is usually a few lines and often removes a data-fetching round
trip entirely — a server component fetches during render with no client waterfall
at all.

Two rules that are not stylistic:

- **Secrets, database handles and server-only SDKs must never be reachable from a
  client component.** Anything imported into a `"use client"` file is bundled and
  shipped. `NEXT_PUBLIC_` in a variable name means public — treat it as printed
  on the page, because it is.
- **Props crossing the server/client boundary are serialised.** Functions, class
  instances, `Date` in some configurations, and `Map`/`Set` do not survive. The
  error is at runtime, and it is confusing.

Composition is the escape hatch worth knowing: a client component can accept
server-rendered `children`, so an interactive wrapper does not force its whole
subtree to the client.

## Data fetching

Fetch where the data is needed. Fetching in a parent and drilling props through
three layers makes every layer a participant in something it does not care about.

**Waterfalls are the default failure.** Sequential dependent `await`s in one
component serialise round trips: three 200ms fetches become 600ms. If they do not
depend on each other, start them together.

```tsx
// 600ms
const user = await getUser(id)
const orders = await getOrders(id)
const prefs = await getPrefs(id)

// 200ms
const [user, orders, prefs] = await Promise.all([getUser(id), getOrders(id), getPrefs(id)])
```

On the client, the equivalent is a `useEffect` that fetches, then a child whose
own `useEffect` fetches once the parent's data arrives. Hoist the dependency or
suspend.

**Every fetch has four states, not one.** Loading, empty, error, and loaded. The
empty state is the one most often missing, and it is what a new user sees first —
a blank screen that looks broken. The error state is the second, and "it just
spins forever" is what a user reports when you did not write it.

**Race conditions in client fetching are real and common.** Type "a", type "ab",
and the response for "a" can arrive second and overwrite the correct results:

```tsx
useEffect(() => {
  const ac = new AbortController()
  fetch(url, { signal: ac.signal }).then(r => r.json()).then(setData).catch(ignoreAbort)
  return () => ac.abort()          // the cleanup is the fix
}, [url])
```

Without the cleanup this is a bug that appears only on a slow connection, which
is why it reaches production.

Prefer a data library — TanStack Query, SWR, the framework's own cache — over
hand-rolled `useEffect` fetching. Caching, deduplication, retry, staleness and
cancellation are all things you would otherwise write badly.

## Caching and revalidation (Next.js specifically)

Caching here is aggressive and the defaults have changed between versions, so
check what this project's version actually does rather than assuming. What stays
true:

- **Know which cache you are hitting** — request memoisation within a render, the
  data cache across requests, the full route cache, and the client router cache.
  "It's stale" is nearly always a different cache from the one you looked at.
- **Static by default means built once.** A page that reads data at build time
  shows build-time data forever until something revalidates it.
- **A mutation must invalidate what it changed.** `revalidatePath` /
  `revalidateTag` after a write, or the user sees their own change not happen —
  the most confusing possible bug, because a refresh fixes it.
- **`cookies()`, `headers()` and `searchParams` opt a route into dynamic
  rendering.** Reading one deep in a component tree can silently make a whole
  route uncacheable.

## State: the most common source of defects

**Derived state is not state.** If a value can be computed from props or existing
state, compute it during render:

```tsx
// Two sources of truth that will disagree.
const [total, setTotal] = useState(0)
useEffect(() => { setTotal(items.reduce(sum, 0)) }, [items])

// One.
const total = items.reduce(sum, 0)
```

The `useState` + `useEffect` pair is the single most common source of
stale-render bugs in this kind of code. It also renders twice, and it shows the
wrong value on the first of them.

**Model the states, do not accumulate booleans.** `isLoading`, `isError`,
`isEmpty` and `data` admit combinations that mean nothing. A discriminated union
of `'idle' | 'loading' | 'ok' | 'error'` admits only the real ones — see
`lens-typescript` for the shape.

**Put state at the level that owns it.** Lifted too high, everything re-renders
and every child needs props it does not care about; kept too low, two siblings
that must agree cannot. URL state — filters, tabs, pagination, the open item —
usually belongs in the URL, because that is what makes it survive a refresh and
be shareable.

**Server data is not client state.** Copying fetched data into `useState` gives
you a second copy that goes stale and a synchronisation problem you now own.

**Never mutate state.** `items.push(x)` then `setItems(items)` is the same
reference, so React skips the render. This is silent.

## Effects

`useEffect` is for synchronising with something outside React: a subscription, a
DOM measurement, a timer, an imperative library, the document title. It is not
for derived values, not for data fetching if you have a better tool, and never
for responding to an event — that belongs in the handler.

- **Every effect that starts something must stop it.** Subscriptions, timers,
  observers, in-flight requests. Under StrictMode in development every effect
  runs twice, which exists precisely to expose a missing cleanup — treat the
  double-run as the bug report it is, not as a nuisance to work around.
- **The dependency array is not advisory.** Removing a dependency to stop a loop
  captures a stale value; the loop is a symptom of state being set from an object
  recreated every render. Fix the identity, not the array.
- **`useEffect` that calls `setState` unconditionally** is an infinite loop with
  extra steps.

## Forms and mutations

- **Disable the submit button while submitting**, or handle the double-click.
  Users double-click. The backend needs idempotency regardless — see
  `lens-backend` — but the client should not be the one causing it.
- **Client validation is UX, server validation is correctness.** Never only the
  first.
- **Show which field failed, and show all of them at once.** One error at a time
  makes a five-field form a five-round-trip conversation.
- **Uncontrolled inputs are fine.** Controlled inputs on every keystroke re-render
  the whole form; for a big form that is felt on a mid-range phone.
- **Optimistic updates need a rollback path**, and the rollback needs to be
  visible. Silently reverting looks like the app losing the user's work.

## Accessibility is correctness

A `div` with an `onClick` is not a button: no keyboard access, no focus ring, no
role, no announcement. This is not polish — it is whether the feature works at
all for some of the people using it, and in many places it is a legal
requirement.

- **Use the element that means what you want.** `button`, `a href`, `label`,
  `nav`, `main`. Every ARIA attribute you avoid needing is one you cannot get
  wrong. The first rule of ARIA is not to use ARIA.
- **Every input needs a label** associated with it — a placeholder is not a
  label; it disappears when you type.
- **Keyboard: everything reachable, focus visible, and order matching the visual
  order.** Test by putting the mouse down and tabbing through.
- **A modal traps focus, returns it on close, and closes on Escape.** All three,
  or it is a keyboard trap.
- **Do not remove focus outlines.** Restyle them.
- **Colour is never the only signal** — an error shown only in red is invisible to
  a colour-blind user and to a screen reader.
- **Images need alt text**; decorative ones need `alt=""`, which is a different
  statement from no attribute.
- **Content that changes without a navigation** — a toast, a validation summary,
  a live result count — needs an `aria-live` region, or a screen-reader user is
  never told it happened.

## Render cost and perceived performance

- **Stable keys on lists.** An array index is not stable under insertion, deletion
  or reorder, and the symptom is inputs keeping the wrong value after a sort.
- **Inline objects and arrow functions are a new identity every render**, which
  defeats memoisation downstream. This matters only where something is memoised.
- **Do not reach for `memo`/`useMemo`/`useCallback` until something is measurably
  slow.** Speculative memoisation is bloat that makes the code harder to change,
  and it has its own cost. Profile first; the answer is usually rendering 5,000
  rows rather than a missing `memo`.
- **Long lists need virtualisation**, not memoisation.
- **Images are usually the page weight.** Correct dimensions to avoid layout
  shift, modern formats, lazy loading below the fold, and a real `sizes`.
- **Reserve space for anything that loads.** A layout that jumps as content
  arrives makes people click the wrong thing.
- **The bundle is a budget.** A date library, an icon set imported wholesale, or a
  chart library on a page with no chart. Check what a route actually pulls in
  before adding a dependency for one helper.

## Before adding a component

Look at how the neighbouring components are built and follow it. A correct
component in a foreign style still costs the next reader.

Then check whether an existing component takes a prop that would cover this case,
before writing a sibling that shares 90% of its body. Two near-identical
components drift, and then the bug is fixed in one of them.

## Review checklist

1. Does `"use client"` sit as low as it can, or has it pulled a subtree along?
2. Can anything server-only be reached from a client component?
3. Are dependent awaits serialised where they could run together?
4. Are loading, empty and error states all written — especially empty?
5. Does a client fetch cancel on unmount or when its input changes?
6. Does a mutation invalidate the cache holding what it changed?
7. Any `useState` kept in sync by a `useEffect` that should be computed?
8. Any state mutated in place before `setState`?
9. Does every effect that starts something clean it up? Does it survive a double-run?
10. Is state at the level that owns it — and should it be in the URL?
11. Can the submit button be double-clicked?
12. Is every interactive element a real element, labelled, and keyboard-reachable?
13. Stable keys on every list?
14. Is any memoisation speculative rather than a response to a measurement?
15. Does this duplicate a component that a prop could have covered?
