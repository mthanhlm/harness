#!/usr/bin/env python3
"""PostToolUse hook: check the file that was just edited, and only that file.

This is the tightest feedback loop the harness has. An error caught here is
fixed in the same turn, in context, by the model that just wrote it — before it
compounds into three more edits built on top of a broken assumption.

Two constraints shape everything here:

- **Latency.** This runs after every single edit, so the budget is a couple of
  seconds. Only the touched file is checked, only with fast tools, and anything
  that overruns its timeout is abandoned rather than waited on.
- **Attribution.** A failure is only reported when `runner` has confirmed the
  edit introduced it. Blocking on a problem the model did not cause teaches it
  to distrust the gate, and teaches the user to disable it.

Formatting is reported but never blocks. It is taste, not fact, and spending a
turn on indentation is exactly the token waste this plugin exists to reduce.
"""

from __future__ import annotations

import sys
from pathlib import Path

from detect import checks_for_file, get_profile
from runner import run_file_check, trim
from state import emit, gates_disabled, guard, read_event, repo_root, session_state, trace

# Tool names whose input carries a file path we should check.
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}


def _target_path(event: dict) -> Path | None:
    if event.get("tool_name") not in EDIT_TOOLS:
        return None
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw)
    return path if path.is_file() else None


def _edit_size(event: dict) -> int:
    """Rough count of lines this edit touched, for the diff budget."""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    if isinstance(content := tool_input.get("content"), str):
        return content.count("\n") + 1
    if isinstance(new := tool_input.get("new_string"), str):
        return new.count("\n") + 1
    if isinstance(edits := tool_input.get("edits"), list):
        return sum(
            str(e.get("new_string", "")).count("\n") + 1 for e in edits if isinstance(e, dict)
        )
    return 0


def _block_reason(failures: list, target: Path) -> str:
    lines = [
        f"The edit to {target.name} introduced problems that were not there before it:",
        "",
    ]
    for result in failures:
        lines.append(f"[{result.label}]")
        for diagnostic in result.new_diagnostics[:12]:
            lines.append(f"  {diagnostic}")
        if not result.new_diagnostics:
            lines.append(trim(result.output, 800))
        lines.append("")
    lines.append(
        "Fix these specific problems. Other diagnostics this tool reports in the same"
        " file already existed at HEAD and are out of scope — do not touch them."
    )
    return "\n".join(lines)


def main() -> int:
    if gates_disabled():
        return 0

    event = read_event()
    target = _target_path(event)
    if target is None:
        return 0

    root = repo_root(event.get("cwd"))
    profile = get_profile(root)
    checks = checks_for_file(profile, str(target))
    if not checks:
        return 0

    results = [run_file_check(check, root, target) for check in checks]
    failures = [r for r in results if r.blocking]
    advisories = [r for r in results if not r.ok and not r.blocking and not r.skipped]

    with session_state(event.get("session_id", "unknown")) as session:
        touched = set(session.get("files_touched") or [])
        touched.add(str(target))
        session["files_touched"] = sorted(touched)
        session["lines_changed"] = int(session.get("lines_changed") or 0) + _edit_size(event)
        checks_stat = session.setdefault("checks", {"run": 0, "failed": 0})
        checks_stat["run"] = int(checks_stat.get("run", 0)) + len(results)
        checks_stat["failed"] = int(checks_stat.get("failed", 0)) + len(failures)

    trace("PostToolUse", event.get("session_id", "?"),
          "blocked" if failures else "ok", file=target.name,
          agent=event.get("agent_type"))

    if failures:
        emit({"decision": "block", "reason": _block_reason(failures, target)})
        return 0

    if advisories:
        # Surfaced to the user, not fed back to the model: formatting is not
        # worth another model turn, and the repo's own format command fixes it.
        names = ", ".join(r.label for r in advisories)
        emit(
            {
                "systemMessage": f"harness: {target.name} is not formatted ({names}).",
                "suppressOutput": True,
            }
        )
    return 0


if __name__ == "__main__":
    with guard("post_edit_check"):
        sys.exit(main())
