#!/usr/bin/env python3
"""Shared state for the harness hooks.

Everything the gates need to remember between hook invocations lives here:
where data is stored, whether the gates are switched off, per-session counters,
and the cached per-repository tool profile.

Two rules govern this module, because hooks run on every edit in every session:

1. Never raise into a hook. A harness that crashes a session is worse than no
   harness. Callers use `guard()` so an unexpected failure exits 0 silently.
2. Never block by default. The previous git hook this replaces was an allowlist
   that blocked every repo it did not know about, and it got disabled. Absence
   of information is never grounds to block.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


# ---------------------------------------------------------------- directories


def data_dir() -> Path:
    """Persistent plugin storage, surviving plugin updates.

    Claude Code sets CLAUDE_PLUGIN_DATA for hook processes. The fallback keeps
    the scripts runnable standalone, which matters for tests.
    """
    raw = os.environ.get("CLAUDE_PLUGIN_DATA")
    if raw:
        return Path(raw)
    return Path.home() / ".claude" / "plugins" / "data" / "harness-local"


def plugin_root() -> Path:
    raw = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent


OFF_MARKER = data_dir() / "off"


def _sub(name: str) -> Path:
    path = data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def profiles_dir() -> Path:
    return _sub("profiles")


def sessions_dir() -> Path:
    return _sub("sessions")


def contracts_dir() -> Path:
    return _sub("contracts")


def ledger_dir() -> Path:
    return _sub("ledger")


# ------------------------------------------------------------- kill switch


def env_disabled() -> bool:
    return os.environ.get("HARNESS_OFF", "").strip() not in ("", "0", "false", "False")


def gates_disabled() -> bool:
    return env_disabled() or OFF_MARKER.exists()


# ------------------------------------------------------------- hook plumbing


def read_event() -> dict[str, Any]:
    """Parse the hook event JSON from stdin, tolerating an empty or broken payload."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def emit(payload: dict[str, Any]) -> None:
    """Write a hook JSON response. Only exit code 0 has its JSON parsed."""
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()


def trace(hook: str, session_id: str, outcome: str, **fields: Any) -> None:
    """Record why a hook decided what it decided.

    A gate that silently does nothing is indistinguishable from a gate that ran
    and found nothing wrong. Reconstructing the difference from session state
    afterwards is guesswork, so each decision point says so at the time. Cheap
    enough to leave on permanently, and the first thing to read when the harness
    appears not to have fired.
    """
    try:
        import time

        line = {
            "at": time.strftime("%H:%M:%S"),
            "hook": hook,
            "session": (session_id or "?")[:8],
            "outcome": outcome,
            **fields,
        }
        path = data_dir() / "gate.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
    except Exception:
        pass  # Diagnostics must never be the reason a hook fails.


@contextmanager
def guard(debug_name: str) -> Iterator[None]:
    """Swallow any unexpected failure so a hook bug can never break a session.

    The traceback goes to a log file rather than stderr: stderr on a non-zero
    exit shows up in the transcript as an error, and a harness that reports its
    own bugs at the user is a harness that gets uninstalled.
    """
    try:
        yield
    except Exception:
        try:
            import traceback

            log = data_dir() / "errors.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as handle:
                handle.write(f"--- {debug_name} ---\n")
                traceback.print_exc(file=handle)
        except Exception:
            pass
        sys.exit(0)


# ------------------------------------------------------------- json helpers


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    """Write atomically so a killed hook cannot leave a truncated state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False, suffix=".tmp"
    )
    try:
        with handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(handle.name, path)
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        raise


# ------------------------------------------------------------- repositories


def repo_root(cwd: str | None = None) -> Path:
    """The git top level, or the working directory when this is not a repo.

    Not being a git repo is normal and must not disable anything; it only means
    diff-based features degrade to counting edits instead.
    """
    start = Path(cwd) if cwd else Path.cwd()
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return start


def repo_key(root: Path) -> str:
    """Stable filename-safe identifier for a repository path."""
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return f"{root.name or 'root'}-{digest}"


# ------------------------------------------------------------- session state


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name) or "unknown"


def _session_path(session_id: str) -> Path:
    return sessions_dir() / f"{_safe(session_id)}.json"


def shards_dir(session_id: str) -> Path:
    return sessions_dir() / f"{_safe(session_id)}.d"


def shard_path(session_id: str, writer: str) -> Path:
    """Where one writer records what it alone changed.

    Writer ids come from a hook payload, so `_safe` is doing security work here
    and not just tidiness: it is what stops an id containing `../` from writing
    outside the sessions directory.
    """
    return shards_dir(session_id) / f"{_safe(writer)}.json"


# Summed or unioned across writers. Everything else is one fact per session.
ACCUMULATORS = ("files_touched", "lines_changed", "checks")

DEFAULT_SESSION: dict[str, Any] = {
    "session_id": "",
    "repo_root": "",
    "files_touched": [],
    "lines_changed": 0,
    "contract": None,
    "consecutive_stop_blocks": 0,
    "edit_gate_prompted": False,
    "checks": {"run": 0, "failed": 0},
}


def writer_id(event: dict[str, Any]) -> str:
    """Which writer a hook payload came from.

    Every hook fired inside a subagent still carries the *main* session's
    `session_id` — `agent_id` is the only field that tells them apart. Without
    this, a worker's edits are indistinguishable from the main thread's and the
    two overwrite each other.
    """
    raw = event.get("agent_id")
    return raw if isinstance(raw, str) and raw.strip() else "main"


def _blank_accumulators() -> dict[str, Any]:
    return {"files_touched": [], "lines_changed": 0, "checks": {"run": 0, "failed": 0}}


def _merge_shards(session_id: str) -> dict[str, Any] | None:
    """Every writer's record, combined. None when no writer has recorded yet.

    The None case matters during a plugin upgrade: a session that started under
    the single-file layout has counters in the session file and no shards, and
    returning zeros here would silently empty its scope fence.
    """
    shards = sorted(shards_dir(session_id).glob("*.json"))
    if not shards:
        return None

    merged = _blank_accumulators()
    files: set[str] = set()
    for shard in shards:
        stored = read_json(shard, default=None)
        if not isinstance(stored, dict):
            continue
        files.update(stored.get("files_touched") or [])
        merged["lines_changed"] += int(stored.get("lines_changed") or 0)
        checks = stored.get("checks")
        if isinstance(checks, dict):
            for key in ("run", "failed"):
                merged["checks"][key] += int(checks.get(key) or 0)
    merged["files_touched"] = sorted(files)
    return merged


def load_session(session_id: str) -> dict[str, Any]:
    stored = read_json(_session_path(session_id), default=None)
    session = json.loads(json.dumps(DEFAULT_SESSION))
    if isinstance(stored, dict):
        session.update(stored)
    merged = _merge_shards(session_id)
    if merged is not None:
        session.update(merged)
    session["session_id"] = session_id
    return session


def _migrate_legacy(session_id: str) -> None:
    """Move a pre-sharding session's counters into the `main` shard.

    Without this, the first hook to open `session_state` after a plugin upgrade
    writes an empty shard of its own. `_merge_shards` then stops returning None,
    and the counters still sitting in the session file are replaced by zeros —
    emptying the scope fence in the middle of a session, which is the failure
    `_merge_shards` returns None to avoid in the first place.
    """
    if _merge_shards(session_id) is not None:
        return
    stored = read_json(_session_path(session_id), default=None)
    if not isinstance(stored, dict):
        return
    legacy = {k: stored[k] for k in ACCUMULATORS if k in stored}
    if legacy:
        write_json(shard_path(session_id, "main"), legacy)


def save_session(session: dict[str, Any], reset: bool = True) -> None:
    """Write `session` as the whole truth, discarding every writer's record.

    Only session start wants this. Anything running while workers exist must use
    `session_state`, which adds a delta rather than overwriting what other
    writers have recorded.

    `reset=False` keeps the shards. A session that is resumed or compacted
    part-way through a fan-out still has workers running, and deleting their
    records destroys the per-worker attribution `SubagentStop` needs to check
    the right files — leaving it to report that a worker wrote nothing.
    """
    session_id = session.get("session_id", "unknown")
    write_json(_session_path(session_id), {k: v for k, v in session.items() if k not in ACCUMULATORS})
    if not reset:
        return

    for stale in shards_dir(session_id).glob("*.json"):
        stale.unlink(missing_ok=True)
    write_json(
        shard_path(session_id, "main"),
        {k: session[k] for k in ACCUMULATORS if k in session},
    )


@contextmanager
def session_state(session_id: str, writer: str = "main") -> Iterator[dict[str, Any]]:
    """Yield the merged session for mutation, then record this writer's delta.

    Parallel workers run this concurrently against one session. Writing the
    mutated view straight back would let whoever saves last erase the rest — and
    because `files_touched` is what the end-of-turn gate checks against the
    plan's scope fence, an erased edit is an edit the scope check never sees and
    the gate then reports clean.

    So a writer records only what it added, in a file of its own, and readers
    merge. Callers see the same dict they always did.
    """
    _migrate_legacy(session_id)
    session = load_session(session_id)
    files_before = set(session.get("files_touched") or [])
    lines_before = int(session.get("lines_changed") or 0)
    checks_before = dict(session.get("checks") or {})
    scalars_before = json.loads(
        json.dumps({k: v for k, v in session.items() if k not in ACCUMULATORS})
    )

    yield session

    stored = read_json(shard_path(session_id, writer), default=None)
    own = stored if isinstance(stored, dict) else _blank_accumulators()

    own["files_touched"] = sorted(
        set(own.get("files_touched") or []) | (set(session.get("files_touched") or []) - files_before)
    )
    own["lines_changed"] = int(own.get("lines_changed") or 0) + (
        int(session.get("lines_changed") or 0) - lines_before
    )
    own_checks = own.setdefault("checks", {"run": 0, "failed": 0})
    checks_now = session.get("checks") or {}
    for key in ("run", "failed"):
        own_checks[key] = int(own_checks.get(key) or 0) + (
            int(checks_now.get(key) or 0) - int(checks_before.get(key) or 0)
        )
    write_json(shard_path(session_id, writer), own)

    # Per-session facts are only ever set from the main thread, so writing them
    # unconditionally would let a worker put a stale copy back. Write on change.
    scalars_now = {k: v for k, v in session.items() if k not in ACCUMULATORS}
    if scalars_now != scalars_before:
        write_json(_session_path(session_id), scalars_now)
