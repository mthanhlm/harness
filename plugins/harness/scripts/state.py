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


def _session_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in session_id) or "unknown"
    return sessions_dir() / f"{safe}.json"


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


def load_session(session_id: str) -> dict[str, Any]:
    stored = read_json(_session_path(session_id), default=None)
    session = json.loads(json.dumps(DEFAULT_SESSION))
    if isinstance(stored, dict):
        session.update(stored)
    session["session_id"] = session_id
    return session


def save_session(session: dict[str, Any]) -> None:
    write_json(_session_path(session.get("session_id", "unknown")), session)


@contextmanager
def session_state(session_id: str) -> Iterator[dict[str, Any]]:
    session = load_session(session_id)
    yield session
    save_session(session)
