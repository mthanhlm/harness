> Python judgement — idiom, error handling, typing, mutability traps, async,
> resource lifetimes, packaging and pytest design that actually catches
> regressions. Loads on .py files and pytest config.
>
> Domain: Python

# Python lens

Python's failure mode is that almost everything runs. A typo becomes an
`AttributeError` at 3am rather than a compile error at 3pm, and a design mistake
runs happily until the data changes shape. Most of what follows is about moving
failures earlier.

## The mutability traps

These survive review because they look like working code.

### Mutable default argument

```python
def add(item, items=[]):        # created ONCE, at def time
    items.append(item)
    return items

add(1)   # [1]
add(2)   # [1, 2]   ← the same list
```

**Tell**: any `=[]`, `={}`, `=set()`, `=datetime.now()` in a signature.
**Instead**: `def add(item, items=None): items = [] if items is None else items`

### Shared mutable in a dataclass

```python
@dataclass
class Config:
    tags: list[str] = []                             # TypeError, mercifully

@dataclass
class Config:
    tags: list[str] = field(default_factory=list)    # correct
```

### Late binding in closures

```python
fns = [lambda: i for i in range(3)]
[f() for f in fns]                        # [2, 2, 2] — not [0, 1, 2]

fns = [lambda i=i: i for i in range(3)]   # bind at definition time
```

**Tell**: a lambda or nested `def` inside a loop referencing the loop variable.
Very common when registering callbacks or building a list of partials.

### Aliasing where a copy was meant

```python
def normalise(config: dict) -> dict:
    config["name"] = config["name"].strip()   # mutates the caller's dict
    return config
```

**Tell**: a function that takes a container and writes to it, but reads as a
transformation. Either name it `normalise_in_place`, or copy first.

`copy.copy` is shallow; `copy.deepcopy` protects nested structures and is
expensive enough to be worth avoiding by not mutating in the first place.

## Errors: catch what you can handle

```python
try:
    value = config["timeout"]
except Exception:              # swallows the NameError three lines down too
    value = 30
```

- **Catch the specific exception**, at the level that can do something about it.
- **`except Exception` catches your own typos**, which is how a broken code path
  ships looking like a default.
- **Bare `except:` also catches `KeyboardInterrupt` and `SystemExit`.** Never.
- **`except ...: pass` is the most expensive line in most codebases.** If a
  failure genuinely does not matter, log at debug and say in a comment why.
- **Chain, do not discard.** `raise ProcessingError(...) from e` keeps the
  original traceback; without `from`, the cause is gone.
- **Raise rather than return `None` for failure.** A caller that forgets to check
  fails three frames later with no connection to the cause.

```python
# The shape that keeps the diagnosis
try:
    parsed = json.loads(raw)
except json.JSONDecodeError as e:
    raise ConfigError(f"{path} is not valid JSON") from e
```

## Resources and lifetimes

Files, sockets, connections, locks and subprocesses go through `with`. A
`close()` skipped when an exception fires is a leak, and under load a leak is an
outage.

```python
f = open(path)                 # leaks the handle on any exception below
data = process(f.read())
f.close()

with open(path) as f:          # closes on every path out
    data = process(f.read())
```

- **`contextlib.contextmanager`** for your own paired setup/teardown.
- **`contextlib.suppress(FileNotFoundError)`** where an exception genuinely is
  the expected case — and it names which one.
- **`ExitStack`** when the number of resources is dynamic.
- **Do not open in `__init__` and close in `__del__`.** `__del__` timing is not
  guaranteed and exceptions inside it are swallowed.

## Typing that does something

Hints are unchecked at runtime, so they earn their place only when a checker
runs. **If `mypy` or `pyright` is not in CI, the annotations are comments** —
useful ones, but not a guarantee.

- **`Optional[T]` means the caller must handle `None`.** A `T | None` return whose
  callers never check is a crash waiting for the empty case.
- **Precise containers.** `dict[str, int]` over `dict`; a `TypedDict` or
  `dataclass` over a dict pretending to be a record.
- **`Any` disables checking for everything it touches.** `object` forces a
  narrowing; use it when the type genuinely is unknown.
- **Narrow with `isinstance`, not `cast`.** `cast` tells the checker to stop
  arguing and changes nothing at runtime.
- **Protocols over ABCs** for "anything with a `.read()`" — structural typing does
  not require the caller to inherit anything.

```python
class Reader(Protocol):
    def read(self, n: int = -1) -> bytes: ...

def load(src: Reader) -> Config: ...       # files, BytesIO, sockets all fit
```

## Async is not a speed-up

`async def` helps only when something actually awaits I/O.

```python
async def fetch_all(urls):
    for url in urls:                      # sequential — no concurrency at all
        results.append(await fetch(url))

async def fetch_all(urls):
    return await asyncio.gather(*(fetch(u) for u in urls))
```

- **A blocking call inside a coroutine stalls the whole event loop** —
  `requests`, `time.sleep`, a large synchronous read, any CPU-heavy work. One
  blocking call makes every concurrent request slow. Use `asyncio.to_thread` or
  the async client.
- **Unawaited coroutines do nothing**, and warn only at garbage collection.
- **`asyncio.gather` cancels siblings on the first exception** unless
  `return_exceptions=True`. Decide which you want; the default surprises people.
- **A task nobody holds a reference to can be collected mid-flight.** Keep the
  reference returned by `create_task`.
- **Timeouts on everything external** — see `lens-resilience`.

**Tell**: `requests.` or `time.sleep(` inside `async def`; a `for` loop with
`await` in the body where the iterations are independent.

## Performance, where it actually matters

- **String concatenation in a loop is quadratic.** `"".join(parts)`.
- **`in` against a list is O(n).** For repeated membership, use a `set`.
- **A query inside a loop is the N+1** — see `lens-database`.
- **Generators for large sequences.** `[x for x in huge]` materialises;
  `(x for x in huge)` does not. `f.readlines()` loads the whole file.
- **`functools.lru_cache` on a pure function with hashable arguments** is nearly
  free. **On a method it keeps `self` alive forever** — a real memory leak.
- **Profile before rewriting anything for speed.** Intuition about where Python
  is slow is usually wrong; `cProfile` is cheap.

## Imports and structure

- **A circular import is a design signal**, not a puzzle to solve with a local
  import. Two modules that need each other usually want a third.
- **Module-level side effects run at import.** Reading config, opening a
  connection or spawning a thread at import time makes the module untestable and
  the failure order-dependent.
- **`from x import *`** breaks every tool that resolves names, including the
  reader.
- **Relative imports within a package, absolute across packages.**

## pytest specifics

General test design is `lens-testing`, which loads on test files and is
language-agnostic. What is Python's own:

- **`pytest.raises` needs `match=`.** Without it the test passes on a completely
  different exception of the same class.

```python
with pytest.raises(ValueError, match="timeout must be positive"):
    Config(timeout=-1)
```

- **`monkeypatch` over manual save-and-restore.** It undoes itself when a test
  fails part-way; hand-rolled teardown does not, and one failure then cascades
  into unrelated tests.
- **`tmp_path` over a fixed temp directory**, or parallel runs collide.
- **Parametrise rather than looping inside a test**, so each case fails by name
  and one failure does not hide the next.
- **Fixture scope is a trap.** A `scope="module"` fixture holding mutable state
  leaks between tests, and the failure depends on ordering.
- **`autouse=True` acts at a distance.** A reader of the test cannot see what set
  it up.
- **Freeze time and randomness**, or the suite fails once a month for reasons
  nobody can reproduce.
- **Assert on the value, not on a mock's call count**, wherever the value is
  available — `assert_called_once_with` passes when the function is a no-op.

## Packaging and environments

- **Pin what you deploy, range what you publish.** A library with pinned
  dependencies is unusable inside someone else's resolution.
- **`pyproject.toml` is the answer.** `setup.py` executing arbitrary code at
  install time is a supply-chain surface.
- **A missing required environment variable should fail loudly at startup**, not
  default to something plausible and misbehave in production.

## Before adding a module

Read the neighbouring modules and follow their layout, naming and error
conventions. Check whether a helper already exists — Python's dynamism makes
duplicate utilities easy to write and hard to notice, and the second copy is
usually found when only one of them gets fixed.

## Review checklist

1. Any mutable default in a signature or dataclass field?
2. Any lambda in a loop capturing the loop variable?
3. Any `except Exception` where a specific one would do? Any `except: pass`?
4. Is a raise chained with `from`, or is the cause discarded?
5. Is every file, socket, lock and connection inside a `with`?
6. Any blocking call inside `async def`? Any sequential `await` loop?
7. Any `Optional` return whose callers do not check?
8. Is `mypy`/`pyright` actually run, or are the hints decoration?
9. Any module-level side effect that runs at import?
10. Does every `pytest.raises` have `match=`?
11. Any `in` against a list in a loop? Any string built with `+=` in a loop?
12. Any `lru_cache` on a method?
