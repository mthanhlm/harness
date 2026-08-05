#!/usr/bin/env python3
"""Run checks, and only blame the edit for failures the edit actually caused.

This module exists because of one failure mode. A repo almost always has some
pre-existing lint noise or a type error nobody has got to yet. A gate that
blocks on those blocks every edit, including edits that fix nothing and break
nothing, and the user disables the harness within a day. That already happened
to the git hook this project replaces.

So a failure is never reported on its own. When a check fails, the same check is
re-run against the file as it exists at HEAD. If it failed there too, the
problem predates the edit and is not the edit's business. Only genuinely new
diagnostics are reported.

The second run costs nothing in the common case, because it only happens when
the first run fails, and most edits pass.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# A check that takes longer than this is not worth the wall-clock on a per-edit
# gate; it is skipped rather than allowed to stall the session.
FILE_CHECK_TIMEOUT = 15
PROJECT_CHECK_TIMEOUT = 300

# Strip the volatile parts of a diagnostic so the same problem matches itself
# across two runs of the same tool on two copies of a file. Two things vary: the
# filename, because the baseline lives in a temporary copy, and every line
# number, because an edit shifts everything below it.
#
# Redaction is done by exact filename rather than by a general path pattern.
# A pattern loose enough to catch every tool's path format is also loose enough
# to erase the difference between two genuinely different diagnostics, and
# erasing that difference means silently swallowing a real new error.
#
# The same argument applies to numbers, and this used to get it wrong: every
# `\b\d+\b` was replaced, so `Expected 2 arguments, but got 1` and `Expected 5
# arguments, but got 3` normalised to the same key. A genuinely new type error
# was then indistinguishable from a pre-existing one and silently swallowed —
# the exact failure the paragraph above exists to avoid.
#
# Only numbers that *move when a line is inserted above them* need erasing, and
# those appear in known positions: attached to the redacted filename, in a
# traceback's `line N`, or in a rich diagnostic's gutter. Numbers in the body of
# a message — arity, error codes, limits — are semantic and are what tells two
# different problems apart, so they stay.
_POSITION_RES = (
    re.compile(r"FILE[(:]\d+(?:[,:]\d+)?\)?"),   # FILE:40:5  FILE(40,5)  FILE:40
    re.compile(r"\bline \d+", re.IGNORECASE),    # Python and shell tracebacks
    re.compile(r"^\s*\d+\s*(?=\|)"),             # rustc/ruff source gutter
)


@dataclass
class Result:
    check: dict[str, Any]
    ok: bool
    output: str
    skipped: str | None = None
    new_diagnostics: list[str] = field(default_factory=list)
    # Whether this failure was confirmed absent on a clean checkout of HEAD.
    # None means the question was never asked or could not be answered — which is
    # a different fact from "no", and reporting the two the same way is how the
    # model gets sent to fix breakage that was there before it arrived.
    new_since_head: bool | None = None

    @property
    def label(self) -> str:
        return str(self.check.get("label", self.check.get("kind", "check")))

    @property
    def blocking(self) -> bool:
        return bool(self.check.get("blocking", True)) and not self.ok and not self.skipped


def _run(argv: list[str], cwd: Path, timeout: int) -> tuple[bool, str, str | None]:
    """`(ok, output, why_it_did_not_really_run)`.

    The third value is the whole point and it used to be missing. A timeout and a
    missing binary both returned "ok", which is right about not blocking and
    wrong about everything else: the caller could not tell them from a genuine
    pass, so a test suite that timed out was reported to the user as *"checks
    that did pass: pytest"*. Silence about a check that never ran is worse than
    the check not existing, because the user believes it.
    """
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # Not a failure — a slow tool must never block — but not a pass either.
        return True, "", f"timed out after {timeout}s"
    except (OSError, ValueError) as exc:
        return True, f"{exc}", f"could not be run: {exc}"
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, output.strip(), None


def _normalize(output: str, redact: tuple[str, ...] = ()) -> dict[str, str]:
    """Map each diagnostic's comparable form to the text the tool actually printed.

    Comparison needs the volatile parts gone; the message shown to the model
    needs them intact, because a type error without its line number is not
    actionable. Keeping both sides of the mapping serves each purpose without
    compromising the other.

    `redact` carries the names the file is known by in this run — absolute path,
    relative path and bare basename — because tools disagree about which they
    print. Longest first, so replacing the basename never truncates a longer
    path that contains it.
    """
    names = sorted({r for r in redact if r}, key=len, reverse=True)
    diagnostics: dict[str, str] = {}
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        key = line
        for name in names:
            key = key.replace(name, "FILE")
        # Filenames first, so a position can be recognised by what precedes it.
        for pattern in _POSITION_RES:
            key = pattern.sub("POS", key)
        diagnostics.setdefault(key, line)
    return diagnostics


# Rich diagnostics render a source excerpt under each message: a gutter, a
# caret row, an arrow to the location. Those lines carry no information on their
# own, and listing them as findings makes two new problems look like eight.
_CONTEXT_RE = re.compile(r"^(\||-->|\^|=|\.\.\.|POS\s*\||N\s*\||\d+\s*\|)")


def _headlines(diagnostics: dict[str, str], keys: set[str]) -> list[str]:
    """The printed form of every non-context diagnostic among `keys`."""
    return [diagnostics[k] for k in sorted(keys) if k in diagnostics and not _CONTEXT_RE.match(k)]


def _names_for(root: Path, target: Path) -> tuple[str, ...]:
    """Every spelling of a path a tool might print in a diagnostic."""
    names = [str(target), target.name]
    try:
        names.append(str(target.resolve().relative_to(root.resolve())))
    except ValueError:
        pass
    return tuple(names)


def _baseline_content(root: Path, target: Path) -> str | None:
    """The file's content at HEAD, or None when there is nothing to compare to.

    A file that is new or untracked has no baseline, which is correct: every
    diagnostic in it is genuinely new.
    """
    try:
        rel = target.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    proc = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{rel.as_posix()}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def _diagnostics_at_head(
    check: dict[str, Any], root: Path, target: Path, timeout: int
) -> tuple[dict[str, str] | None, str | None]:
    """Re-run the check against the pre-edit version of the file.

    Returns `(diagnostics, why_unknown)`. Those are three different answers and
    conflating two of them was a real bug: a baseline that *could not be run*
    returned an empty diagnostic set, identical to a baseline that ran and found
    nothing — so every pre-existing problem in the file looked new and the edit
    was blocked for breakage it did not cause. That is the single failure this
    whole module exists to prevent, and a slow type-checker was enough to trigger
    it.

    The temporary copy is written beside the original so that everything a tool
    resolves by location — tsconfig, ruff settings, eslint config, import
    roots — resolves exactly as it does for the real file.
    """
    content = _baseline_content(root, target)
    if content is None:
        return None, None  # No baseline exists: the file is new, so all of it is.
    handle = None
    try:
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=".harness-baseline-",
            suffix=target.suffix,
            delete=False,
        )
        with handle:
            handle.write(content)
        copy = Path(handle.name)
        argv = [a.replace("{file}", str(copy)) for a in check["argv"]]
        _, output, skipped = _run(argv, root, timeout)
        if skipped:
            return None, skipped
        return _normalize(output, _names_for(root, copy)), None
    except OSError as exc:
        return None, f"baseline copy failed: {exc}"
    finally:
        if handle is not None:
            Path(handle.name).unlink(missing_ok=True)


def run_file_check(check: dict[str, Any], root: Path, target: Path) -> Result:
    argv = [a.replace("{file}", str(target)) for a in check["argv"]]
    ok, output, skipped = _run(argv, root, FILE_CHECK_TIMEOUT)
    if skipped:
        return Result(check, True, output, skipped=skipped)
    if ok:
        return Result(check, True, output)

    current = _normalize(output, _names_for(root, target))
    baseline, unknown = _diagnostics_at_head(check, root, target, FILE_CHECK_TIMEOUT)
    if unknown:
        # The baseline could not be established, so nothing here is attributable.
        # Never block on a comparison that did not happen.
        return Result(check, True, output, skipped=f"no baseline: {unknown}")
    if baseline is None:
        # New or untracked file: nothing in it predates the edit.
        return Result(check, False, output, new_diagnostics=_headlines(current, set(current)))

    new = set(current) - set(baseline)
    if not new:
        return Result(
            check,
            True,
            output,
            skipped="pre-existing: the same diagnostics are present at HEAD",
        )
    headlines = _headlines(current, new)
    if not headlines:
        # Everything new was source context around a pre-existing problem,
        # which means the edit only shifted line numbers.
        return Result(check, True, output, skipped="pre-existing: only line positions moved")
    return Result(check, False, output, new_diagnostics=headlines)


def run_project_check(check: dict[str, Any], root: Path, timeout: int = PROJECT_CHECK_TIMEOUT) -> Result:
    ok, output, skipped = _run(list(check["argv"]), root, timeout)
    return Result(check, ok, output, skipped=skipped)


# A baseline run costs about as much as the failing run did. Paying that to
# check a two-second type-check is obviously worth it; paying it to re-run a
# five-minute build is not, and the answer arrives too late to matter.
BASELINE_COST_CEILING = 120


def project_check_at_head(check: dict[str, Any], root: Path, timeout: int) -> bool | None:
    """Whether this project check also fails on a pristine checkout of HEAD.

    Answers the question the per-file gate answers with `git show`: did this
    session actually cause the failure? At project scope a single file is not
    enough, so the comparison needs a whole tree — hence a detached worktree,
    which builds one without disturbing anything the user has in progress.

    Returns True when HEAD is broken too (so the session is not to blame), False
    when HEAD is clean, and None when the question could not be answered.
    """
    import shutil as _shutil
    import subprocess as _sp

    if not (root / ".git").exists():
        return None

    tmp = Path(tempfile.mkdtemp(prefix="harness-head-"))
    worktree = tmp / "tree"
    try:
        created = _sp.run(
            ["git", "-C", str(root), "worktree", "add", "--detach", "--quiet", str(worktree), "HEAD"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if created.returncode != 0:
            return None

        # Dependencies are not tracked in git, so a fresh checkout has none.
        # Linking them in is what makes a build or test run at all comparable.
        for vendored in ("node_modules", ".venv", "venv", "vendor"):
            source = root / vendored
            if source.is_dir():
                try:
                    (worktree / vendored).symlink_to(source, target_is_directory=True)
                except OSError:
                    pass

        argv = [a.replace(str(root), str(worktree)) for a in check["argv"]]
        ok, _, skipped = _run(argv, worktree, timeout)
        if skipped:
            return None  # Could not answer. Same bug as above if reported as clean.
        return not ok
    except (OSError, _sp.SubprocessError):
        return None
    finally:
        _sp.run(
            ["git", "-C", str(root), "worktree", "remove", "--force", str(worktree)],
            capture_output=True,
            timeout=60,
            check=False,
        )
        _sp.run(["git", "-C", str(root), "worktree", "prune"], capture_output=True, timeout=30, check=False)
        _shutil.rmtree(tmp, ignore_errors=True)


def trim(text: str, limit: int = 2000) -> str:
    """Keep tool output short enough to be worth reading in a hook response."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… ({len(text) - limit} more characters)"
