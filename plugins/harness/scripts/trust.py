#!/usr/bin/env python3
"""Whether this repository's own commands may run.

The harness executes what it detects, and some of what it detects is written by
the repository rather than by this plugin: `.harness.json` check entries,
`package.json` scripts, and any tool resolved out of `node_modules/.bin` or
`.venv/bin`. All three are files that arrive with a clone. Cloning a repository
and editing one file in it was enough to run arbitrary commands with the user's
full permissions, with no prompt and nothing in the transcript.

So repo-authored commands wait until the user says so, once per repository.

This does not violate the rule that the harness never blocks by default. That
rule is about not blocking the user's *edit*, and it is not blocked: plugin
composed checks — `py_compile`, `node --check`, `bash -n` — still run, and the
degraded state is simply fewer checks, which is what already happens in a repo
with no tooling. Declining to execute a stranger's command is not blocking
anything of the user's.

Trust is keyed on a digest of the command set, so a repository that changes what
it runs needs approving again. Otherwise approving a repo once would be a
standing grant to whatever it later decides to execute.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from state import _sub, read_json, repo_key, repo_root, write_json


def trust_dir() -> Path:
    return _sub("trust")


def _record_path(root: Path) -> Path:
    return trust_dir() / f"{repo_key(root)}.json"


def repo_authored(profile: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in profile.get("checks", []) if c.get("source") == "repo"]


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "absent"


def _payload(root: Path | None, check: dict[str, Any]) -> str:
    """What the command would actually execute, not just how it is spelled.

    Two of the three doors are a stable indirection: `npm run --silent test`
    never changes when the body of that script does, and
    `node_modules/.bin/eslint` never changes when its bytes do. Hashing the
    invocation alone made approving a repo once a standing grant on whatever it
    later decided to run — which is the opposite of what this file promises.
    """
    argv = check.get("argv") or []
    # json, not " ".join: ["sh","-c","a b"] and ["sh","-c a","b"] are different
    # commands and joined to the same string.
    parts = [json.dumps(argv)]
    if root is None:
        return "|".join(parts)

    for manifest in ("package.json", ".harness.json"):
        path = root / manifest
        if path.is_file() and any(manifest.split(".")[0] in a or "run" in a for a in argv):
            parts.append(_file_hash(path))
    if argv:
        first = Path(argv[0])
        if first.is_absolute() and first.is_file() and str(first).startswith(str(root.resolve())):
            parts.append(_file_hash(first))
    return "|".join(parts)


def digest(profile: dict[str, Any], root: Path | None = None) -> str:
    """A fingerprint of every command this repo would have us run, and of what
    those commands would execute."""
    marks = sorted(_payload(root, c) for c in repo_authored(profile))
    return hashlib.sha256("\n".join(marks).encode("utf-8")).hexdigest()[:16]


def is_trusted(root: Path, profile: dict[str, Any]) -> bool:
    if not repo_authored(profile):
        return True  # Nothing to trust; do not ask about an empty set.
    stored = read_json(_record_path(root), default=None)
    return isinstance(stored, dict) and stored.get("digest") == digest(profile, root)


def grant(root: Path, profile: dict[str, Any]) -> dict[str, Any]:
    record = {
        "repo_root": str(root),
        "digest": digest(profile, root),
        "commands": sorted(" ".join(c.get("argv") or []) for c in repo_authored(profile)),
    }
    write_json(_record_path(root), record)
    return record


def revoke(root: Path) -> bool:
    path = _record_path(root)
    existed = path.is_file()
    path.unlink(missing_ok=True)
    return existed


def main() -> int:
    from detect import get_profile

    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    root = repo_root()
    profile = get_profile(root, refresh=True)
    pending = repo_authored(profile) + profile.get("withheld_checks", [])

    if action == "grant":
        if not pending:
            print(f"trust: {root} defines no commands of its own — nothing to approve.")
            return 0
        record = grant(root, {"checks": pending})
        print(f"trust: approved {len(record['commands'])} command(s) for {root}:")
        for command in record["commands"]:
            print(f"  {command}")
        print(f"  recorded in {trust_dir()}")
        return 0

    if action == "revoke":
        print(f"trust: {'revoked' if revoke(root) else 'was not trusted'} — {root}")
        return 0

    trusted = not profile.get("withheld_checks")
    print(f"trust: {root}")
    print(f"  repo-authored commands: {len(pending)}")
    print(f"  trusted: {'yes' if trusted else 'no'}")
    for check in pending:
        mark = "runs" if trusted else "WITHHELD"
        print(f"  [{mark}] {check.get('label')}: {' '.join(check.get('argv') or [])}")
    if not trusted:
        print("  run `/harness:trust` to approve these")
    # Named because a grant written to the wrong data directory reports success
    # and changes nothing: the hooks read `CLAUDE_PLUGIN_DATA`, a shell does not
    # inherit it, and the fallback is a different directory entirely. Showing
    # the path is what turns that from silent into obvious.
    print(f"  reading {trust_dir()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
