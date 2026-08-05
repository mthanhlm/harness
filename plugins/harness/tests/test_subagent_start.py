"""Handing each agent the domain knowledge its job needs, from code.

This replaced an instruction, and the reason is the shape of its failure. An
agent told to run `lenses.py` can skip it or pass the wrong paths, and a
reviewer with no lens still produces a review — same shape, same length, same
confidence. Nothing downstream can tell.

Selecting a lens is arithmetic over paths, and the book measures what happens
when a model does arithmetic it could have been handed: a status bar maintained
by code beat one maintained by a frontier model, and the model-maintained
version scored below having no status bar at all.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import subagent_start

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _lenses(text: str) -> list[str]:
    return re.findall(r'<lens name="([^"]+)">', text)


def test_a_pre_code_agent_gets_the_requirements_lens_with_no_diff(tmp_path):
    """The challenger runs before the code exists. Routing it on a diff returns
    nothing, and an empty block reads as "no domain knowledge applies"."""
    text, _ = subagent_start.context_for("challenger", tmp_path)

    assert "lens-requirements" in _lenses(text)
    assert "# Requirements lens" in text


def test_a_reviewer_is_routed_on_what_actually_changed(git_repo):
    """The other half. After the code exists the changed files are a fact, and
    a fact is what the selection should be made from."""
    (git_repo / "schema.ts").write_text("export const users = {}\n", encoding="utf-8")

    text, _ = subagent_start.context_for("reviewer-correctness", git_repo)

    assert "lens-typescript" in _lenses(text)


def test_a_subject_lens_arrives_whatever_the_diff_contains(git_repo):
    """A security review is usually of a route handler, not of anything under
    `auth/`. Path matching alone drops the one lens it cannot work without."""
    (git_repo / "route.ts").write_text("export function GET() {}\n", encoding="utf-8")

    assert "lens-security" in _lenses(subagent_start.context_for("reviewer-security", git_repo)[0])
    assert "lens-testing" in _lenses(subagent_start.context_for("reviewer-tests", git_repo)[0])


def test_the_number_of_lenses_is_bounded(git_repo):
    """A wide diff must not fill the agent's context with domains it will not
    use — the window is the budget every judgement spends from."""
    for name in ("a.py", "b.ts", "c.tsx", "Dockerfile", "d.sql", "e.test.ts", "f.graphql"):
        (git_repo / name).write_text("x\n", encoding="utf-8")

    text, _ = subagent_start.context_for("reviewer-correctness", git_repo)

    assert 0 < len(_lenses(text)) <= subagent_start.MAX_LENSES


def test_no_match_offers_the_catalogue_rather_than_silence(tmp_path):
    """An agent that knows a catalogue exists can ask for a page. One told
    nothing assumes there was nothing to have."""
    text, _ = subagent_start.context_for("refuter", tmp_path)

    assert _lenses(text) == []
    assert "lens-database" in text and "lens-frontend" in text
    assert "# Database lens" not in text, "the catalogue printed whole bodies"


def test_the_content_is_tagged_as_knowledge_to_apply(tmp_path):
    """Structure carries meaning: a named XML block tells the model what it is
    looking at, where a bare paste makes it infer. And it is told to apply the
    content rather than restate it, or the lens comes back as the review."""
    text, _ = subagent_start.context_for("designer", tmp_path)

    assert text.startswith("<domain_knowledge>")
    assert text.rstrip().endswith("</domain_knowledge>")
    assert "do not restate it" in text


def test_the_hook_emits_the_documented_shape(hook_env, tmp_path):
    """`additionalContext` under a `hookSpecificOutput` naming the event. Any
    other shape is ignored in silence — the agent starts, with no lens, and
    nothing reports that the hook did nothing."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "subagent_start.py")],
        input=json.dumps({
            "session_id": "s1",
            "hook_event_name": "SubagentStart",
            "agent_type": "harness:challenger",
            "cwd": str(tmp_path),
        }),
        capture_output=True, text=True, timeout=30, env=hook_env,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    assert "lens-requirements" in payload["hookSpecificOutput"]["additionalContext"]


def test_an_unknown_agent_does_not_break_the_spawn(hook_env, tmp_path):
    """This runs on the way into somebody else's task. An agent that does not
    start is worse than one that starts without its lens."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "subagent_start.py")],
        input=json.dumps({
            "session_id": "s1", "hook_event_name": "SubagentStart",
            "agent_type": "", "cwd": str(tmp_path),
        }),
        capture_output=True, text=True, timeout=30, env=hook_env,
    )

    assert proc.returncode == 0
