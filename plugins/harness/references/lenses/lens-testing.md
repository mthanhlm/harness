> Test design judgement — whether a test would fail if the code were wrong,
> which cases actually break, what to mock, how to kill flakiness, and how to
> keep a suite honest as it grows. Language-agnostic, examples in Python and
> TypeScript. Loads automatically in tests/ directories and on test and spec files.
>
> Domain: test design

# Testing lens

This is the single home for test-design judgement, and it is loaded by whoever
is **writing** tests as well as whoever is reviewing them. A worker with no
test-design knowledge writes tests that pass and prove nothing, which is worse
than no tests at all: no tests is a known gap, and a green suite that checks
nothing is a false statement about the system that everyone downstream believes.

## The whole standard, in one question

**What change to the implementation would make this fail?**

Ask it before writing the assertion, not after. If the honest answer is "nothing
much", the test is decoration — it costs a run on every commit forever and buys
nothing. Delete it or make it real.

## Mutation testing: the only way to know

Answering that question in your head is a guess. Answering it with the code is
five seconds of work:

1. Break the implementation deliberately — invert a condition, drop a clause,
   return a constant, delete a line.
2. Run the test.
3. It must go **red**, and the failure message must name the thing you broke.
4. Put the implementation back.

A test you have never seen fail is a test you have no evidence works. This is the
highest-value habit on this page, and it is worth doing on every test that guards
something that matters.

The failure-message part is not optional. A test that goes red with
`AssertionError: assert False` told you nothing, and the next person to see it
has to re-derive the whole intent. `assert user.role == "admin", "the promotion
did not apply"` costs six words and survives being read by someone who has never
seen the code.

**Mutations worth trying, in rough order of what they catch:**

| Mutation | Catches |
|---|---|
| Invert a boolean condition | Tests that never exercise the false branch |
| Return a constant from the function | Assertions that happen to match the fixture |
| Delete the line the test is about | Tests asserting on a side effect that came from elsewhere |
| Swap two arguments of the same type | Tests with symmetric fixtures — `f(1, 1)` |
| Change a boundary `<` to `<=` | Tests that never touch the boundary |
| Remove the error handling | Tests that never reach the error path |

If a mutation survives you have found either a missing test or a test asserting
the wrong thing. Both are worth knowing before the bug ships.

One trap: **check that your mutation is not inert.** Inserting a line that a
later line overwrites mutates nothing, and the test passing tells you nothing
about the test. If a mutation survives, first confirm it actually changed the
behaviour — print the value, or run the code by hand.

## Tests that prove nothing — with the shape to recognise them by

### The tautological assertion

```python
# Can never fail: `"skills"` is absent, so `.get` returns [], and
# `"lens-" not in []` is True whatever the file says.
assert "lens-" not in frontmatter(path).get("skills", [])
```

The fix is to make the subject explicit, so an empty subject becomes visible:

```python
declared = frontmatter(path).get("skills", [])
assert declared, f"{path} declares no skills; this test would pass vacuously"
assert not [s for s in declared if s.startswith("lens-")]
```

Any assertion over a collection has this failure mode. `all(...)` over an empty
list is `True`. `for x in []: assert ...` runs zero assertions and passes. If a
test can pass because the thing it iterates was empty, assert that it is not.

### Computing the expected value the way the code does

```python
def test_total():
    assert cart.total() == sum(i.price * i.qty for i in cart.items)
```

The test and the bug now agree with each other. If `total()` forgets tax, so does
the assertion. Write the expected value as a literal — `assert cart.total() ==
4750` — even when it feels brittle. Brittle is the point: it breaks when the
answer changes, which is exactly when you want to be told.

### Mocking the thing under test

Mock what you do not own: the network, the clock, the filesystem, the payment
provider, the model. Mocking the subject means asserting that your mock does what
you told it to do.

```python
# Tests the mock, not the code.
monkeypatch.setattr(billing, "charge", lambda *a: Receipt(ok=True))
assert billing.charge(card, 100).ok
```

The same shape appears one level out and is harder to see: a test of `checkout()`
that mocks `billing.charge` still tests `checkout`, which is fine — until the
assertion is `charge.called`, at which point it has stopped testing checkout's
behaviour and started testing its call sequence.

### Asserting on internals

Call counts, private attributes and mock interaction records pin the
implementation rather than the behaviour. The test then breaks on every refactor
that changes nothing observable, and survives every real bug that changes
behaviour while keeping the call sequence intact. `assert mock.call_count == 3` is
almost always the wrong assertion; `assert charged_once(account)` means something.

There is a narrow exception: when the call **is** the behaviour. "We must not
charge the card twice" is a call-count property, and asserting it is correct.

### Testing at the wrong level of the error

```python
with pytest.raises(Exception):     # passes on a typo in the test itself
    parse(bad_input)
```

`pytest.raises` without `match=` passes on any exception, including the
`NameError` from your own misspelled variable. Constrain it:

```python
with pytest.raises(ParseError, match="unclosed bracket at line 3"):
    parse(bad_input)
```

Same in TypeScript: `expect(fn).toThrow()` passes on any throw;
`expect(fn).toThrow(/unclosed bracket/)` does not. And `await expect(p).rejects`
without the `await` passes on a rejected promise nobody looked at.

### The over-broad snapshot

A snapshot of an entire rendered page fails on every unrelated change and gets
regenerated without being read — at which point it asserts whatever the code
currently does, bug included. Snapshot the smallest thing whose exact shape
matters. If reviewing a snapshot diff is "looks fine, accept", it is not a test.

### Several reasons to fail in one test

A failure should name its own cause. A test asserting six unrelated things names
none of them, and it stops at the first, so the other five go unchecked until
someone fixes the first. Split by *reason to fail*, not by line count.

### Passing on the first run, for new behaviour

Suspicious rather than damning, but it means nobody watched it fail — and the
usual cause is that the test does not reach the new code at all.

## The cases that actually break

Coverage percentage measures lines executed, not cases considered. 100% line
coverage is compatible with never testing a single boundary. Work the shape of
the input instead:

| Shape | The question |
|---|---|
| **Empty** | Zero items, empty string, empty file. Does the loop body ever run? |
| **One** | Off-by-one and "first iteration" logic hide here |
| **Many** | Pagination, batching, anything that changes at a limit |
| **Absent** | `null`, missing key, unset env var, a column that is `NULL` |
| **Malformed** | Wrong type, truncated, wrong encoding, hostile |
| **Duplicate** | Two identical rows, the same request twice |
| **Boundary** | Both sides of every threshold, and the threshold itself |
| **Concurrent** | Two writers, a reader during a write, a retry landing twice |
| **Second call** | State left behind by the first — the most-missed case |
| **Error path** | Nobody exercised it by hand either, so it is most likely wrong |

The error path deserves the emphasis. Happy paths get exercised constantly during
development; the `except` branch is often first executed in production. Test it by
making the dependency fail, not by calling the handler directly — calling the
handler proves the handler works, not that it is reachable.

## Test doubles: which one, and when

| Double | What it is | Use when |
|---|---|---|
| **Stub** | Returns canned values | The subject just needs *some* input |
| **Fake** | Working lightweight implementation — in-memory DB | The dependency's behaviour matters across several calls |
| **Mock** | Records calls, asserts on them | The call itself is the behaviour under test |
| **Spy** | Real thing, calls recorded | You need the real behaviour *and* the record |

Default to a **fake** over a mock. A mock encodes your belief about how the
dependency behaves; a fake encodes the behaviour. When the belief is wrong the
mock keeps the test green and production broken — the most common way a
well-tested integration still fails.

The strongest version: run the real dependency where you can. A test against a
real Postgres in a container catches the `NULL` handling, the collation and the
unique-index violation that an in-memory fake never will.

## Determinism, and the four sources of flake

A flaky test is worse than no test: it trains everyone to re-run CI without
reading the failure, and then a real failure gets re-run too.

| Source | Fix |
|---|---|
| **Time** | Inject the clock. Never `sleep()` to wait — poll a condition with a deadline |
| **Randomness** | Seed it, and print the seed in the failure output |
| **Ordering** | Shared state between tests. A fresh fixture per test, not a module-level object |
| **Concurrency** | A real race, in the code or the test. Do not add a sleep; find it |

If a suite passes locally and fails on CI, suspect order first. Run it shuffled
and run it with `--lf` to confirm. A suite that only passes in one order has a
dependency between tests, and it will eventually bite in a way that has nothing
to do with tests.

**Never fix a flake by retrying it.** A retry converts "this is broken 5% of the
time" into "this is broken 5% of the time and nobody knows".

## Testing concurrency

Threads and sleeps do not reproduce races reliably. What does:

- **Separate processes**, where the failure is two processes reading a file
  before either writes. Threads share an interpreter and can mask it entirely.
- **A barrier** that forces both parties to arrive before either proceeds.
- **Injecting the interleaving** — a hook the test controls that yields at the
  exact point the race lives.

If a concurrency test passes 1000 times that is weak evidence; if it fails once
that is strong evidence. Treat the asymmetry accordingly, and never delete a
concurrency test because it "only fails sometimes".

## What not to test

- **The framework.** That the ORM saves a row, that the router routes. Its
  maintainers test it, and your version breaks on their upgrades.
- **Getters, constructors, pure data.** Nothing to get wrong.
- **The same path twice.** A second test asserting a covered path costs a run
  forever and catches nothing new.
- **Implementation you expect to change this week.** Test the interface it will
  still have.

## Regression tests

When a bug reaches production, the test that should have caught it is almost
always a missing **case**, not a missing file. Add it to the existing test for
that behaviour, named for the symptom rather than the ticket —
`test_a_second_submit_does_not_double_charge`, not `test_bug_4471`. The name is
what a reader six months later has to work with.

Write the test first, watch it fail against the unfixed code, then fix. That
sequence is mutation testing with the mutation supplied for free.

## Suite health

A suite is a thing you maintain, and it has its own failure modes:

- **Runtime.** Past a few minutes people stop running it locally and start
  finding out on CI. Split the slow ones behind a marker rather than letting the
  whole suite rot.
- **Flake rate.** Track it. A 1% per-test flake rate gives a 63% chance of at
  least one failure in a 100-test run.
- **Tests nobody can explain.** A test whose name and body do not say what
  behaviour it protects cannot be safely deleted or safely changed, so it stays
  forever and blocks refactors.
- **Assertions per test, rising.** Tests are being appended to rather than
  written.

## Non-deterministic subjects

Testing something allowed to vary — a model, a ranking, a heuristic — needs a
different standard, and `lens-evaluation` is the page for it. The one thing to
carry here: **a single passing run of a non-deterministic subject is not a pass.**
Repeat it. Reliability across repeats collapses far faster than single-run
accuracy suggests — a step that succeeds 60% of the time succeeds five times
running about 8% of the time.

## Before adding a test

Check what the existing tests already cover, in the file that already covers it.
Then ask the question at the top of this page, and answer it by breaking the code.

## Review checklist

1. Would this fail if the implementation were wrong? Which mutation kills it?
2. Can it pass vacuously — empty collection, absent key, `all()` over nothing?
3. Does the expected value come from a literal, or from the code's own logic?
4. Is the thing under test mocked?
5. Does the failure message name the cause?
6. Is the error path reached by making a dependency fail, or only called directly?
7. Are empty / one / many / absent / boundary / second-call covered?
8. Is there a mock where a fake would encode the real behaviour?
9. Any `sleep`, unseeded randomness, real clock, or shared mutable fixture?
10. Does it assert on internals a harmless refactor would break?
11. Does the suite still pass in a different order?
12. Is this a second test for a path already covered?
