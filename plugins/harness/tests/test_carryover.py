"""What a session is told after its context was compacted away.

A compaction summarises the transcript and throws the rest away. The gates do
not care — the contract and the shards are both on disk, and they go on
enforcing whatever was agreed. The *model* cares: it comes back knowing it is
mid-task and not knowing what the task was, so it re-reads files to rebuild what
it already agreed to, which is how a context that was just compacted refills.

So the facts the session cannot reconstruct cheaply are handed back to it. All
of them are read from state that already exists; nothing here is persisted.
"""

from __future__ import annotations

import re
from pathlib import Path

from conftest import SCRIPTS, run_hook

CONTRACT = """# Plan: teach the gate to say what it means

status: approved
verdict: patch

## Goal
Stop the gate reporting a pass it never ran.

## Scope
Files this will change:
- `plugins/harness/scripts/stop_gate.py` — the four latches
- `plugins/harness/scripts/session_start.py` — the carry-over

Explicitly NOT changing:
- `plugins/harness/scripts/runner.py` — needs its own plan

## Risks
None worth naming.
"""


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "plugins" / "harness" / "scripts").mkdir(parents=True)
    return root


def write_contract(session_id: str, text: str = CONTRACT) -> None:
    import contract as contract_mod

    path = contract_mod.contract_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def start(hook_env: dict, root: Path, source: str, session_id: str = "sess") -> str:
    response = run_hook(
        "session_start.py",
        {"session_id": session_id, "cwd": str(root), "source": source},
        hook_env,
        root,
    )
    return response.get("hookSpecificOutput", {}).get("additionalContext", "")


def test_a_compacted_session_is_told_what_it_agreed_to(data_dir, hook_env, tmp_path):
    """The whole point, in one assertion set.

    Every fact here is one the model would otherwise pay to rediscover: the goal
    it is working towards, the verdict that settled how, the files it may touch,
    the files it promised not to, and which of the agreed ones it has already
    edited. Re-reading the plan file costs a tool call and a few thousand tokens;
    this costs a few hundred, once, at the point the context is smallest.
    """
    root = repo(tmp_path)
    write_contract("sess")
    edited = root / "plugins" / "harness" / "scripts" / "stop_gate.py"
    edited.write_text("x = 1\n", encoding="utf-8")

    import state

    with state.session_state("sess") as session:
        session["files_touched"] = [str(edited)]
        session["repo_root"] = str(root)

    context = start(hook_env, root, "compact")

    assert "Stop the gate reporting a pass it never ran." in context, context
    assert "patch" in context
    assert "plugins/harness/scripts/stop_gate.py" in context
    assert "plugins/harness/scripts/session_start.py" in context
    assert "plugins/harness/scripts/runner.py" in context, "the excluded list must survive"
    assert "not yet edited" in context


def _under(context: str, label: str) -> list[str]:
    """The entries rendered beneath one label — the truncation tail excluded.

    The tail is indented like an entry, so counting it as one would make an
    assertion about the cap off by exactly the thing that reports the cap.
    """
    lines = context.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith(label))
    out = []
    for line in lines[start + 1 :]:
        if not line.startswith("    "):
            break
        entry = line.strip()
        if entry.startswith("- "):
            out.append(entry[2:].strip())
    return out


def test_an_edited_file_is_not_reported_as_outstanding(data_dir, hook_env, tmp_path):
    """Which list a file lands in, asserted by name.

    Merely asserting that both labels appear is decoration: swap the two lists
    and it still passes, and so does deleting the path comparison entirely and
    calling every file outstanding. Both mutations were tried and the whole
    suite stayed green — which made the only non-trivial logic in the renderer
    unprotected, on the feature whose entire job is to stop the model redoing
    work it has already done.
    """
    root = repo(tmp_path)
    write_contract("sess")
    edited = root / "plugins" / "harness" / "scripts" / "stop_gate.py"
    edited.write_text("x = 1\n", encoding="utf-8")

    import state

    with state.session_state("sess") as session:
        session["files_touched"] = [str(edited)]

    context = start(hook_env, root, "compact")

    assert _under(context, "In scope, already edited") == [
        "plugins/harness/scripts/stop_gate.py"
    ]
    assert _under(context, "In scope, not yet edited") == [
        "plugins/harness/scripts/session_start.py"
    ]


def test_clear_does_not_report_edited_files_as_outstanding(data_dir, hook_env, tmp_path):
    """`clear` resets the counters, and the snapshot must be read before it does.

    `fresh` is true for `clear`, so `files_touched` is emptied — and reading it
    afterwards told the model that every file it had already edited was still
    outstanding. On the one source where this feature matters most, that is not
    a missing fact but a wrong one: an instruction to redo finished work.
    """
    root = repo(tmp_path)
    write_contract("sess")
    edited = root / "plugins" / "harness" / "scripts" / "stop_gate.py"
    edited.write_text("x = 1\n", encoding="utf-8")

    import state

    with state.session_state("sess") as session:
        session["files_touched"] = [str(edited)]

    context = start(hook_env, root, "clear")

    assert _under(context, "In scope, already edited") == [
        "plugins/harness/scripts/stop_gate.py"
    ]


def test_a_blocking_gate_is_named_in_the_snapshot(data_dir, hook_env, tmp_path):
    """Coming back mid-block without being told what is red wastes the next turn."""
    root = repo(tmp_path)
    write_contract("sess")

    import state

    with state.session_state("sess") as session:
        session["files_touched"] = [str(root / "plugins" / "harness" / "scripts" / "stop_gate.py")]
        session["heavy_blocked"] = {"deadbeef": "pytest"}

    context = start(hook_env, root, "compact")

    assert "currently blocking on: pytest" in context


def test_a_lesson_is_named_in_every_session_not_only_a_resumed_one(data_dir, hook_env, tmp_path):
    """Lessons are loaded like the tool profile, not like the carry-over
    snapshot — `startup` must see them too, since a fresh session has just as
    much to gain from a durable lesson as a resumed one does."""
    root = repo(tmp_path)
    (root / ".harness").mkdir()
    (root / ".harness" / "lessons.md").write_text(
        "# Lessons\n\n## L1 · 2026-08-03 · the newest thing learned\n\nBody.\n",
        encoding="utf-8",
    )

    context = start(hook_env, root, "startup")

    assert "the newest thing learned" in context


def test_a_repo_supplied_lessons_file_cannot_flood_the_context(data_dir, hook_env, tmp_path):
    """`.harness/lessons.md` is a repository file, and a clone's belongs to whoever wrote it.

    Not a trust boundary — this plugin deliberately has none, and the same repo
    can already execute code through `.harness.json`. It is a budget: every
    other value in this block is capped, and one uncapped entry could put
    hundreds of thousands of characters into the context of every session.
    """
    root = repo(tmp_path)
    (root / ".harness").mkdir()
    (root / ".harness" / "lessons.md").write_text(
        f"# Lessons\n\n## L1 · 2026-08-03 · {'A' * 200_000}\n\nBody.\n", encoding="utf-8"
    )

    context = start(hook_env, root, "startup")

    assert len(context) < 6000, f"one repo-supplied entry must not flood the context: {len(context)}"


def test_the_excluded_list_survives_when_everything_else_is_trimmed(data_dir, hook_env, tmp_path):
    """Truncation has to have a priority, and this is it.

    `session_start.py`'s docstring is right that long always-on context makes the
    important lines get ignored, so the block is capped. But real licensing
    edits have already landed because the "NOT changing" list was mis-parsed,
    which makes it the single line least safe to drop. A cap that trims the
    cheapest thing first would trim exactly that.
    """
    scoped = "\n".join(f"- `src/generated/mod{n:03}.ts` — regenerated" for n in range(200))
    write_contract(
        "sess",
        "# Plan: big\n\nstatus: approved\nverdict: patch\n\n## Goal\nBulk work.\n\n"
        f"## Scope\n{scoped}\n\nExplicitly NOT changing:\n- `LICENSE` — never\n\n## Risks\nNone.\n",
    )
    root = repo(tmp_path)

    context = start(hook_env, root, "compact")

    assert "LICENSE" in context, "the excluded list must never be the part that is trimmed"
    assert len(context) < 6000, f"the snapshot must stay capped, got {len(context)}"
    # The bound above only proves a cap exists — MAX_LISTED could grow to 100
    # and still fit. This proves the cap is the one that was agreed.
    assert len(_under(context, "In scope, not yet edited")) <= 12
    assert re.search(r"and \d+ more", context), (
        "a truncated list with no tail reads as a complete one"
    )


def test_every_excluded_entry_is_carried_or_counted(data_dir, hook_env, tmp_path):
    """The list the plan is least able to afford losing must not be quietly cut.

    The obvious reuse here was the bullet parser once used for the roadmap's
    deferred entries, which capped at four because a roadmap entry wanted four
    interesting deferrals in prose. Applied to a scope fence it dropped six of
    this change's own ten exclusions — and because the cap landed before the
    renderer could count what it lost, no "and N more" tail fired either. Ten
    bullets in, four out, reading as all of them.
    """
    excluded = "\n".join(f"- `path/to/excluded{n}.py` — because" for n in range(10))
    write_contract(
        "sess",
        "# Plan: many exclusions\n\nstatus: approved\nverdict: patch\n\n## Goal\nWork.\n\n"
        f"## Scope\n- `src/a.py`\n\nExplicitly NOT changing:\n{excluded}\n\n## Risks\nNone.\n",
    )
    root = repo(tmp_path)

    context = start(hook_env, root, "compact")

    rendered = _under(context, "Agreed NOT to change")
    tail = re.search(r"and (\d+) more", context)
    accounted = len(rendered) + (int(tail.group(1)) if tail else 0)
    assert accounted == 10, f"10 exclusions went in, {accounted} came out: {rendered}"


def test_a_renderer_that_raises_still_leaves_the_tool_profile(data_dir, hook_env, tmp_path):
    """The contract is hand-editable, so one day it will break the carry-over
    renderer.

    `guard()` would catch the exception and exit 0 — and take the tool profile
    with it, which every session depends on and which has nothing to do with the
    contract. Passing a malformed contract does not exercise this: `\\x00` is
    valid UTF-8 and simply parses to nothing. Only a renderer that genuinely
    throws does.
    """
    root = repo(tmp_path)
    write_contract("sess")
    broken = SCRIPTS / "contract.py"
    saved = broken.read_text(encoding="utf-8")
    broken.write_text(
        saved.replace("def section(text: str, heading: str) -> str:",
                      "def section(text: str, heading: str) -> str:\n    raise RuntimeError('boom')"),
        encoding="utf-8",
    )
    try:
        context = start(hook_env, root, "compact")
    finally:
        broken.write_text(saved, encoding="utf-8")

    assert "harness active" in context
    assert "Carried over" not in context


def test_a_lessons_renderer_that_raises_still_leaves_the_tool_profile(data_dir, hook_env, tmp_path):
    """The same failure, on the lessons side rather than the carry-over side.
    `.harness/lessons.md` is just as hand-editable as the contract, and it is
    read on every session, not only a resumed one."""
    root = repo(tmp_path)
    broken = SCRIPTS / "lessons.py"
    saved = broken.read_text(encoding="utf-8")
    broken.write_text(
        saved.replace("def entries(root: str | Path | None = None) -> list[Lesson]:",
                      "def entries(root=None):\n    raise RuntimeError('boom')\n\n"
                      "def _unused_entries(root: str | Path | None = None) -> list[Lesson]:"),
        encoding="utf-8",
    )
    try:
        context = start(hook_env, root, "startup")
    finally:
        broken.write_text(saved, encoding="utf-8")

    assert "harness active" in context


def test_a_resumed_session_gets_the_same_snapshot(data_dir, hook_env, tmp_path):
    """Resume is where the value actually is.

    In 170 recorded session starts there were 48 resumes and 3 clears against 6
    compacts. Wiring this to `compact` alone would serve one case in nine of the
    ones that need it, which is how a fix aimed at a felt problem misses the
    common one.
    """
    root = repo(tmp_path)
    write_contract("sess")

    for source in ("resume", "clear"):
        context = start(hook_env, root, source)
        assert "Stop the gate reporting a pass it never ran." in context, source


def test_a_fresh_session_is_not_given_a_stale_plan(data_dir, hook_env, tmp_path):
    """A startup has nothing to carry over, and saying otherwise is worse than silence.

    Session ids are reused across `--continue`, and a contract file outlives the
    turn that wrote it. Injecting it on `startup` would hand a brand-new session
    an old plan as though it were current, which is the one failure mode that
    turns this feature into a liability.
    """
    root = repo(tmp_path)
    write_contract("sess")

    context = start(hook_env, root, "startup")

    assert "Stop the gate reporting a pass it never ran." not in context
    assert "harness active" in context, "the normal tool profile must still be emitted"


def test_a_session_with_no_contract_says_nothing_extra(data_dir, hook_env, tmp_path):
    """Most sessions have no plan, and they must not pay for this at all."""
    root = repo(tmp_path)

    context = start(hook_env, root, "compact")

    assert "Scope" not in context
    assert "harness active" in context


def test_an_unapproved_plan_is_carried_over_as_unapproved(data_dir, hook_env, tmp_path):
    """A pending plan is a strictly weaker state than no plan, and must read as one.

    The scope fence is inert until `status: approved`, so a compacted session
    that is handed a plan without being told it was never approved will believe
    it is fenced when it is not. The Stop gate already says this out loud; the
    snapshot must not contradict it.
    """
    root = repo(tmp_path)
    write_contract("sess", CONTRACT.replace("status: approved", "status: pending"))

    context = start(hook_env, root, "compact")

    assert "not approved" in context.lower(), context


def test_the_snapshot_survives_a_contract_that_is_not_markdown(data_dir, hook_env, tmp_path):
    """A hook that raises is worse than a hook that says nothing.

    The contract is a file a human can edit, so it will eventually be malformed.
    `guard()` would swallow the traceback and exit 0 — but that also drops the
    tool profile every session depends on, so the renderer has to degrade rather
    than raise.
    """
    root = repo(tmp_path)
    write_contract("sess", "\x00 not markdown at all")

    context = start(hook_env, root, "compact")

    assert "harness active" in context
