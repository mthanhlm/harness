---
name: lens-testing
description: Test design judgement — whether a test would fail if the code were wrong, which cases actually break, what to mock, and how to keep a suite honest. Language-agnostic. Loads automatically in tests/ directories and on test and spec files.
paths:
  - "**/tests/**"
  - "**/__tests__/**"
  - "**/test_*.py"
  - "**/*_test.py"
  - "**/*.test.ts"
  - "**/*.test.tsx"
  - "**/*.spec.ts"
  - "conftest.py"
user-invocable: false
---

# Testing lens

This is the single home for test-design judgement. It is loaded by whoever is
**writing** tests as well as by whoever is reviewing them — a worker with no
test-design knowledge writes tests that pass and prove nothing, which is worse
than no tests because it reads as coverage.

## The whole standard, in one question

**What change to the implementation would make this fail?**

Ask it before writing the assertion. If the honest answer is "nothing much", the
test is decoration — it costs a run on every commit and buys nothing. Delete it
or make it real.

The strongest version of this is to check rather than assume: break the
implementation deliberately and watch the test go red. A test you have never
seen fail is a test you have no evidence works.

## Tells of a test that proves nothing

- **Asserts on internals.** Call counts, private attributes, and mock
  interactions pin the implementation, not the behaviour. The test then breaks on
  every refactor and survives every real bug.
- **Mocks the thing under test.** Mock what you do not own — the network, the
  clock, the filesystem, the payment provider. Mocking the subject means testing
  the mock.
- **Asserts the expected value by computing it the same way the code does.** The
  test and the bug then agree with each other.
- **Passes on the first run, on new behaviour.** Suspicious rather than damning,
  but it means nobody watched it fail.
- **Several reasons to fail in one test.** A failure should name its own cause;
  a test asserting six things names none of them.
- **Catches a broad exception, or asserts only that "an error was raised".** In
  Python `pytest.raises` needs `match=`; without it the test passes on the wrong
  exception entirely.

## The cases that actually break

Coverage percentage measures lines executed, not cases considered. Work the
shape of the input instead: **empty, one, many, absent, malformed, duplicate,
and the error path** — the last being the one usually left untested and the one
most likely to be wrong, because nobody exercised it by hand either.

Then the ones specific to the domain: boundary values either side of every
threshold, concurrent access where two things write, and what happens on the
second call.

## Fixtures and setup

Setup shared between tests is a dependency between tests. Prefer a fixture that
builds a fresh thing per test over state that accumulates. A suite that fails
only when run in a different order has an ordering bug, and it will surface on
CI rather than locally.

Name the fixture after what it *is*, not what it does — `broken_at_head` says
what a reader needs; `setup_2` does not.

## Before adding a test

Check what the existing tests already cover, in the file that already covers it.
A second test asserting the same path costs a run forever and catches nothing
new. And if a bug reached production, the test that should have caught it is
usually a missing *case*, not a missing file.
