---
name: lens-frontend
description: React and Next.js judgement — component boundaries, server versus client, data fetching, state, accessibility and render cost. Loads automatically when working on .tsx/.jsx/.css files or in components/.
paths:
  - "**/*.tsx"
  - "**/*.jsx"
  - "**/*.css"
  - "**/*.scss"
  - "components/**"
user-invocable: false
---

# Frontend lens

## Server and client are a decision, not a default

In the App Router every component is a server component until `"use client"`
says otherwise. That directive is load-bearing and spreads: it applies to
everything the file imports. Before adding it, check whether only a leaf
actually needs interactivity — pushing the boundary down is usually a few lines
and often removes a data-fetching round trip entirely.

Secrets, database handles and server-only SDKs must never end up in a file that
is reachable from a client component.

## Fetch where the data is needed

Fetching in a parent and drilling props through three layers makes every layer a
participant in something it does not care about. Fetch in the component that
renders it. A waterfall of dependent awaits in one component is worse than
parallel fetches in several.

## State that is derived is not state

If a value can be computed from props or existing state, compute it. A `useState`
kept in sync by a `useEffect` is a bug waiting for the two to disagree, and it is
the single most common source of stale-render defects in this kind of code.

Reach for `useEffect` only for genuine synchronisation with something outside
React. Data fetching, derived values and event responses are not that.

## Accessibility is correctness

A `div` with an `onClick` is not a button: no keyboard access, no focus ring, no
role. Use the element that means what you want, label inputs, keep focus visible.
This is not polish — it is whether the feature works for some of the people using
it.

## Render cost

Lists need stable keys, and an array index is not stable under insertion or
reorder. An object or arrow function created inline in a prop is a new
identity every render, which defeats memoisation downstream. Do not reach for
`memo`/`useMemo` until something is actually slow — speculative memoisation is
bloat that also makes the code harder to change.

## Before adding a component

Look at how the neighbouring components are built and follow it. A correct
component in a foreign style still costs the next reader. Check whether an
existing one takes a prop that would cover this case before writing a sibling
that shares 90% of its body.
