---
name: lens-python
description: Python judgement — idiom, error handling, typing, async, resource lifetimes and pytest design that actually catches regressions. Loads automatically on .py files and pytest config.
paths:
  - "**/*.py"
  - "**/*.pyi"
  - "pytest.ini"
  - "pyproject.toml"
  - "conftest.py"
user-invocable: false
---

# Python lens

## Let the language do the work

Comprehensions over accumulate-in-a-loop. `pathlib` over string paths.
`enumerate` and `zip` over index arithmetic. `dataclass` over a dict pretending
to be a record. f-strings over concatenation. Each of these removes a place a
bug can live, not just characters.

A mutable default argument (`def f(items=[])`) is shared across every call. It
is still the most common Python bug that survives review.

## Catch what you can handle

`except Exception:` around a block that only expects `KeyError` swallows the
typing errors and the `KeyboardInterrupt` too. Catch the specific exception, at
the level that can actually do something about it. Never `except: pass` — if a
failure genuinely does not matter, the comment must say why.

Raising a specific exception beats returning `None` for failure: the caller that
forgets to check `None` fails silently three frames later.

## Resources and lifetimes

Files, sockets, database connections and locks go through `with`. A `close()`
that is skipped when an exception fires is a leak, and under load a leak is an
outage.

## Async is not a speed-up

`async def` only helps when something actually awaits I/O. A blocking call
inside a coroutine — `requests`, `time.sleep`, heavy CPU work — stalls the whole
event loop, which is worse than not being async at all.

## Tests that would fail if the code were wrong

This is the whole standard. Before writing an assertion, ask what change to the
implementation would make it fail. If the honest answer is "nothing much", the
test is decoration.

- Assert on behaviour and returned values, not on internal call counts.
- Mock what you do not own — the network, the clock, the filesystem. Mocking the
  thing under test means testing the mock.
- One reason to fail per test, so a failure names its own cause.
- Cover the edges that actually break: empty, one, many, absent, malformed,
  duplicate, and the error path — which is the one usually left untested.
- `pytest.raises` needs `match=`, or it passes on the wrong exception.

## Before adding a module

Look at the neighbouring modules and follow their layout, naming and error
conventions. Check whether a helper for this already exists — Python's dynamism
makes duplicate utilities easy to write and hard to notice.
