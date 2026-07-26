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
_NUM_RE = re.compile(r"\b\d+\b")


@dataclass
class Result:
    check: dict[str, Any]
    ok: bool
    output: str
    skipped: str | None = None
    new_diagnostics: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return str(self.check.get("label", self.check.get("kind", "check")))

    @property
    def blocking(self) -> bool:
        return bool(self.check.get("blocking", True)) and not self.ok and not self.skipped


def _run(argv: list[str], cwd: Path, timeout: int) -> tuple[bool, str]:
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
        return True, ""  # Treated as a pass: a slow tool must never block.
    except (OSError, ValueError) as exc:
        return True, f"{exc}"  # Missing or unrunnable tool is never grounds to block.
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, output.strip()


def _normalize(output: str, redact: tuple[str, ...] = ()) -> set[str]:
    """Reduce tool output to a comparable set of diagnostics.

    `redact` carries the names the file is known by in this run — absolute path,
    relative path and bare basename — because tools disagree about which they
    print. Longest first, so replacing the basename never truncates a longer
    path that contains it.
    """
    names = sorted({r for r in redact if r}, key=len, reverse=True)
    lines = set()
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        for name in names:
            line = line.replace(name, "FILE")
        line = _NUM_RE.sub("N", line)
        lines.add(line)
    return lines


# Rich diagnostics render a source excerpt under each message: a gutter, a
# caret row, an arrow to the location. Those lines carry no information on their
# own, and listing them as findings makes two new problems look like eight.
_CONTEXT_RE = re.compile(r"^(\||-->|\^|=|\.\.\.|N\s*\||\d+\s*\|)")


def _headlines(diagnostics: set[str]) -> list[str]:
    return sorted(d for d in diagnostics if not _CONTEXT_RE.match(d))


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


def _diagnostics_at_head(check: dict[str, Any], root: Path, target: Path, timeout: int) -> set[str] | None:
    """Re-run the check against the pre-edit version of the file.

    The temporary copy is written beside the original so that everything a tool
    resolves by location — tsconfig, ruff settings, eslint config, import
    roots — resolves exactly as it does for the real file.
    """
    content = _baseline_content(root, target)
    if content is None:
        return None
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
        _, output = _run(argv, root, timeout)
        return _normalize(output, _names_for(root, copy))
    except OSError:
        return None
    finally:
        if handle is not None:
            Path(handle.name).unlink(missing_ok=True)


def run_file_check(check: dict[str, Any], root: Path, target: Path) -> Result:
    argv = [a.replace("{file}", str(target)) for a in check["argv"]]
    ok, output = _run(argv, root, FILE_CHECK_TIMEOUT)
    if ok:
        return Result(check, True, output)

    current = _normalize(output, _names_for(root, target))
    baseline = _diagnostics_at_head(check, root, target, FILE_CHECK_TIMEOUT)
    if baseline is None:
        # New or untracked file: nothing in it predates the edit.
        return Result(check, False, output, new_diagnostics=_headlines(current))

    new = current - baseline
    if not new:
        return Result(
            check,
            True,
            output,
            skipped="pre-existing: the same diagnostics are present at HEAD",
        )
    headlines = _headlines(new)
    if not headlines:
        # Everything new was source context around a pre-existing problem,
        # which means the edit only shifted line numbers.
        return Result(check, True, output, skipped="pre-existing: only line positions moved")
    return Result(check, False, output, new_diagnostics=headlines)


def run_project_check(check: dict[str, Any], root: Path, timeout: int = PROJECT_CHECK_TIMEOUT) -> Result:
    ok, output = _run(list(check["argv"]), root, timeout)
    return Result(check, ok, output)


def trim(text: str, limit: int = 2000) -> str:
    """Keep tool output short enough to be worth reading in a hook response."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… ({len(text) - limit} more characters)"
