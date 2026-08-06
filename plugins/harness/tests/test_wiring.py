"""Does every name in this plugin resolve to the thing it names?

Nothing here tests behaviour. It tests that the wiring is connected at all,
because every failure in this class is silent: a role whose agent file does not
exist, a `skills:` entry with a typo, a hook pointing at a moved script. None of
them raise. The role simply does less, or nothing, and the run looks clean —
which is indistinguishable from a change with nothing wrong with it.

This is cheap insurance on the parts of the plugin that are three separate
declarations of the same fact.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parents[1]
AGENTS = PLUGIN / "agents"
SKILLS = PLUGIN / "skills"
REGISTRY = json.loads((PLUGIN / "crew" / "registry.json").read_text(encoding="utf-8"))
HOOKS = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))

AGENT_FILES = sorted(AGENTS.glob("*.md"))
SKILL_FILES = sorted(SKILLS.glob("*/SKILL.md"))


def frontmatter(path: Path) -> dict[str, object]:
    """Enough YAML for what this plugin actually writes: scalars and dash lists."""
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    assert match, f"{path} has no frontmatter"
    out: dict[str, object] = {}
    key = None
    for line in match.group(1).splitlines():
        if item := re.match(r"^\s+-\s+(.+?)\s*$", line):
            if key:
                out.setdefault(key, []).append(item.group(1).strip('"\''))
        elif pair := re.match(r"^(\w[\w-]*):\s*(.*)$", line):
            key = pair.group(1)
            value = pair.group(2).strip()
            if value:
                out[key] = value
    return out


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_an_agents_declared_name_matches_its_filename(path):
    """The `name` is what the Task tool resolves; the filename is what a human
    looks for. When they disagree, the agent is unfindable by one of them."""
    assert frontmatter(path).get("name") == path.stem


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_a_skills_declared_name_matches_its_directory(path):
    assert frontmatter(path).get("name") == path.parent.name


@pytest.mark.parametrize("role", REGISTRY["roles"], ids=lambda r: r["name"])
def test_every_registry_role_has_an_agent_file(role):
    assert (AGENTS / f"{role['name']}.md").is_file()


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_every_preloaded_skill_exists(path):
    """A `skills:` typo does not raise — the agent just runs without the domain
    knowledge it was designed around."""
    for name in frontmatter(path).get("skills", []):
        assert (SKILLS / name / "SKILL.md").is_file(), f"{path.stem} declares missing skill {name}"


def test_every_hook_script_exists():
    """A moved or misspelled script means the hook never fires, and a gate that
    silently does nothing looks exactly like a gate that found nothing."""
    for event, groups in HOOKS["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                script = Path(hook["args"][0].replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN)))
                assert script.is_file(), f"{event} points at missing {script}"


LENSES = PLUGIN / "references" / "lenses"


def test_every_lens_in_the_registry_has_a_page_behind_it():
    """A registry entry with no page is a lens that routes and then loads
    nothing — `lenses.py` skips what it cannot read rather than crashing inside
    somebody else's task, so this is the only place the gap is visible."""
    for lens in REGISTRY["lenses"]:
        assert (LENSES / f"{lens['name']}.md").is_file(), lens["name"]


def test_no_lens_page_is_stranded_without_a_registry_entry():
    """The other direction. A page nothing routes to is dead weight that reads
    as maintained — and the registry is now the only routing table there is,
    since the paths used to be declared twice and drifted."""
    named = {lens["name"] for lens in REGISTRY["lenses"]}
    for page in LENSES.glob("*.md"):
        assert page.stem in named, f"{page.stem} is a lens nothing can select"


def test_a_lens_is_knowledge_and_not_a_skill():
    """They were nine skills, and every agent that might need one declared six
    in its frontmatter — fourteen entries in the model-facing listing for a
    plugin with one flow. They carry no tools and no model; being read as
    information is what they always were."""
    assert not list(SKILLS.glob("lens-*")), "a lens has come back as a skill"
    for path in AGENT_FILES:
        declared = frontmatter(path).get("skills", [])
        # `"lens-" not in declared` is the tempting form and is a tautology —
        # it asks whether the exact string is an element, which it never is.
        assert not [s for s in declared if s.startswith("lens-")], (
            f"{path.stem} declares a lens as a skill again: {declared}"
        )


SIBLINGS = sorted({p.parent.name for p in SKILL_FILES} | {p.stem for p in AGENT_FILES})

# The ways a skill actually tells the model to dispatch something by name.
# The first version matched one — ``` `implement` skill ``` — and missed
# `Invoke the implement skill` (no backticks), `` `implement` with the Skill
# tool `` (capital S), `Skill: implement` (the real invocation form quoted from
# transcripts), and every agent name. Five of six natural regressions passed.
#
# A backticked name is how this codebase writes a dispatch; an unbackticked one
# is usually prose *about* a skill ("loaded by the plan skill", "from the
# architect subagent") and must not be flagged, or the test cries wolf and gets
# deleted. The unbackticked form is caught only behind an imperative verb, which
# is exactly how the regression read: "Invoke the implement skill".
_INVOCATIONS = (
    r"`{name}`[ \t]+(?:skill|(?:sub)?agent)\b",
    r"\b(?:skill|(?:sub)?agent)[ \t]+`{name}`",
    r"\bskill[ \t]*:[ \t]*`?{name}`?\b",
    r"\bsubagent_type[ \t]*[:=][ \t]*[\"'`]?{name}\b",
    r"\b(?:invoke|use|launch|run|dispatch|call|send)[ \t]+(?:it[ \t]+to[ \t]+)?"
    r"(?:the[ \t]+)?`?{name}`?[ \t]+(?:skill|(?:sub)?agent)\b",
)


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_a_skill_never_tells_the_model_to_dispatch_a_sibling_by_bare_name(path):
    """`implement` does not resolve; `harness:implement` does.

    This is worth a test because it fails *quietly and plausibly*. An
    unresolvable name does not raise — the model falls back to reading the
    skill's own SKILL.md, which looks like it worked and is not the same thing.
    A read document is information; an invoked skill is instructions. It went
    unnoticed through a full day of real use, in a file that states the prefix
    rule for agents three hundred lines above getting it wrong for skills.
    """
    text = path.read_text(encoding="utf-8")
    for name in SIBLINGS:
        if name == path.parent.name:
            continue
        for template in _INVOCATIONS:
            pattern = template.format(name=re.escape(name))
            for match in re.finditer(pattern, text, re.IGNORECASE):
                prefix = text[max(0, match.start() - 12) : match.start() + len(name) + 2]
                assert "harness:" in prefix, (
                    f"{path.parent.name} dispatches `{name}` without the harness: prefix"
                    f" — {text[max(0, match.start() - 40): match.end() + 10]!r}"
                )


# `harness:worker` in prose, `harness:reviewer-*` as a wildcard, and
# `harness:plan`'s own name are all references rather than typos. A trailing `*`
# is a family, and it is checked by prefix instead.
_PREFIXED_RE = re.compile(r"\bharness:([a-z][\w-]*)(\*?)")


def _label(path: Path) -> str:
    """`SKILL14` names nothing. A skill is identified by its directory."""
    return path.parent.name if path.stem == "SKILL" else path.stem


@pytest.mark.parametrize("path", SKILL_FILES + AGENT_FILES, ids=_label)
def test_every_prefixed_name_resolves_to_something_that_exists(path):
    """The other half of the bare-name test, and the half that rots.

    The sibling test catches `implement` written without its prefix. It cannot
    catch `harness:reuse-auditor` written *correctly* — for an agent that was
    deleted last week. Both fail the same silent way: the dispatch does not
    raise, the model reads something else or nothing, and the run looks clean.

    This is the failure mode of deleting an agent, which is a thing this plugin
    does whenever three partial jobs get merged into one. Three skills referred
    to the three agents removed in 0.8, in three files nobody would have thought
    to open.
    """
    text = path.read_text(encoding="utf-8")
    for name, wildcard in set(_PREFIXED_RE.findall(text)):
        if wildcard:
            assert any(s.startswith(name) for s in SIBLINGS), (
                f"{path.stem} references the family `harness:{name}*`, which matches no"
                f" agent or skill"
            )
            continue
        assert name in SIBLINGS, (
            f"{path.stem} dispatches `harness:{name}`, which is neither an agent nor a"
            f" skill in this plugin — available: {', '.join(SIBLINGS)}"
        )


def test_session_start_announces_the_checks_a_repo_will_actually_run(
    data_dir, hook_env, tmp_path
):
    """The context has to name the commands, because nothing else will.

    Driven as a process rather than grepped for. A source-level assertion passes
    happily while the feature is inert — the previous version of this test
    regexed for a `systemMessage` assignment, and blanking the list it described
    left all 173 tests green.
    """
    from conftest import run_hook

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    response = run_hook(
        "session_start.py", {"session_id": "s", "cwd": str(repo), "source": "startup"},
        hook_env, repo,
    )

    context = response["hookSpecificOutput"]["additionalContext"]
    project_line = next(
        (line for line in context.splitlines() if "when the turn ends" in line), ""
    )
    assert "pytest" in project_line, (
        "a check the harness runs at the end of the turn must be announced on the"
        f" end-of-turn line, not merely somewhere in the blob: {context!r}"
    )


# Not only the directory helpers. Anything that resolves a path *under*
# `data_dir()` inherits the same dependency on `CLAUDE_PLUGIN_DATA`, and a script
# reaching state through `contract.contract_path` rather than `contracts_dir`
# was invisible here — `report_page.py` wrote its page into `harness-local/`
# against a contract living in the real plugin directory, found nothing, and
# reported a path to an empty page. Blaming the script would be the wrong
# lesson: it used the right helper. The check was looking one layer too high.
_STATE_RESOLVERS = (
    "data_dir",
    "ledger_dir",
    "contracts_dir",
    "profiles_dir",
    "shards_dir",
    "contract_path",
    "shard_path",
    "load_session",
    "session_state",
)
_SHELLS_OUT = re.compile(r"^(.*)python3 \"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/(\w+\.py)\"", re.MULTILINE)


def _touches_plugin_state(source: str) -> bool:
    """Whether a script *uses* a state directory, as opposed to mentioning one.

    This was a substring search, and substring searches cannot tell code from
    prose. A script that names all five directories in a docstring in order to
    say it deliberately touches none of them had that sentence read as proof
    that it did, and failed the very check it was disclaiming. The natural
    response is to reword the true comment until the test goes green, which
    trades an accurate docstring for a passing run.

    Names bound or referenced in code count; names inside a string or comment do
    not. An unparseable script is assumed to touch state, because a check that
    waves through what it cannot read is worse than one that over-reports.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _STATE_RESOLVERS:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _STATE_RESOLVERS:
            return True
        if isinstance(node, ast.alias) and node.name in _STATE_RESOLVERS:
            return True
    return False


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_a_skill_that_shells_out_to_plugin_state_passes_the_data_directory(path):
    """A shell does not inherit `CLAUDE_PLUGIN_DATA`; the hooks are given it.

    So a skill command that reads or writes plugin state resolves a *different*
    directory than every hook, and reports confident success against it.
    The report answered "No sessions recorded yet" over a ledger holding
    ten sessions and $830.

    Which scripts need it is derived from their source rather than listed here,
    so a script that starts touching plugin state is covered without anyone
    remembering to update this.
    """
    for indent, script in _SHELLS_OUT.findall(path.read_text(encoding="utf-8")):
        source = (PLUGIN / "scripts" / script).read_text(encoding="utf-8")
        if not _touches_plugin_state(source):
            continue
        assert "CLAUDE_PLUGIN_DATA" in indent, (
            f"{path.parent.name} runs {script}, which reads plugin state, without passing"
            " CLAUDE_PLUGIN_DATA — it will resolve a different directory than the hooks"
        )


@pytest.mark.parametrize(
    "source, touches",
    [
        ("from state import data_dir\nx = data_dir()\n", True),
        ("import state\nx = state.profiles_dir()\n", True),
        ('"""Reads no plugin state — no data_dir, no profiles_dir."""\nx = 1\n', False),
        ("# contracts_dir is deliberately not used here\nx = 1\n", False),
        ("def f(:\n", True),
    ],
    ids=["call", "attribute", "docstring", "comment", "unparseable"],
)
def test_plugin_state_usage_is_told_apart_from_merely_naming_it(source, touches):
    """The helper guarding the data-directory check needs its own guard.

    Exactly one shell-out in the whole plugin currently reaches the assertion
    this feeds, so narrowing the helper by one branch would switch the check off
    across the board and nothing else would notice. The docstring and comment
    cases are the ones that regressed: a script explaining that it touches no
    plugin state was read as proof that it did.
    """
    assert _touches_plugin_state(source) is touches


def _git(*args: str) -> str | None:
    """Git output, or None when git cannot answer — no repo, no git, no HEAD."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO), *args],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


_VERSION_RE = re.compile(r'"version"\s*:\s*"([^"]+)"')
_MANIFEST = "plugins/harness/.claude-plugin/plugin.json"


@pytest.mark.prepublish
def test_changing_plugin_code_without_bumping_the_version_is_caught_here():
    """An unbumped version makes a published fix a silent no-op.

    Marked `prepublish` and deselected by default. This is the one check here
    that asserts a property of the working tree rather than of the code, and that
    made it fatal to leave in the default suite: it fails on *any* modification
    under `plugins/`, so every mutation in a sweep read as caught whether or not
    a real test noticed. A sweep of 18 scored 18/18 against a suite that was
    genuinely blind to several of them. This plugin's own agents mutation-test to
    decide whether a test is worth keeping, so a tautology here corrupts the
    method the whole review path rests on.

    Run it with `python3 -m pytest -m prepublish` before publishing.

    `claude plugin marketplace update` refreshes marketplace metadata but does
    not re-fetch a plugin whose version string it has already seen. It finds the
    version directory present, leaves it, and prints "Successfully updated" —
    while the running hooks go on executing the previous code. This plugin's own
    history records a full publish cycle lost to it, rediscovered only by listing
    the cache directory by hand.

    What makes it worth a test rather than a note is how it presents: the fix
    appears not to work, which is indistinguishable from the fix being wrong. So
    the next move is to go and change correct code.

    Deliberately checked against the working tree rather than the last commit, so
    it fires while the change is still being made and the bump is one edit — not
    after a push, when the only remedy is another commit.
    """
    changed = _git("diff", "--name-only", "HEAD", "--", "plugins")
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", "plugins")
    if changed is None or untracked is None:
        pytest.skip("not a git checkout, so there is no baseline to compare against")

    if not (changed + untracked).strip():
        return  # Nothing under plugins/ has changed; no bump is owed.

    committed = _git("show", f"HEAD:{_MANIFEST}")
    if committed is None:
        return  # The manifest is itself new; there is no previous version to move.

    was = _VERSION_RE.search(committed)
    now = _VERSION_RE.search((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert was and now, f"{_MANIFEST} has no version string"
    assert was.group(1) != now.group(1), (
        f"plugin code changed but plugin.json is still {now.group(1)} — installs will"
        " silently keep the old version. Bump it before publishing."
    )


def test_write_capable_agents_are_named_here_on_purpose():
    """Only `worker` may edit. A second write-capable agent is a decision, not
    an accident, and the parallel-edit safety was designed around exactly one."""
    writers = {
        p.stem
        for p in AGENT_FILES
        if {"Write", "Edit"} & {t.strip() for t in str(frontmatter(p).get("tools", "")).split(",")}
    }
    assert writers == {"worker"}, f"unexpected write-capable agents: {writers - {'worker'}}"


# Every hook event this plugin subscribes to. Listed here so that adding one
# without a script, or renaming a script out from under one, is caught — and so
# that the set is visible in one place rather than spread across a JSON file.
SUBSCRIBED = {
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
    # `PostToolUse` fires only when the tool succeeded. A shell command that
    # exits non-zero has usually still written something — a build that emits
    # `dist/` and then fails its own check — and without the failure event those
    # files reach no gate at all: the pre-command sample is simply overwritten
    # by the next command.
    "PostToolUseFailure",
    "SubagentStart", "SubagentStop", "PostCompact", "Stop", "SessionEnd",
}


def test_the_hook_events_are_the_ones_intended():
    """A typo in an event name does not raise — the hook is registered against
    an event that never fires, and the gate silently does nothing forever. This
    is the one class of wiring error the plugin loader will not report, because
    unknown event names are simply ignored.
    """
    assert set(HOOKS["hooks"]) == SUBSCRIBED, (
        f"unexpected {set(HOOKS['hooks']) - SUBSCRIBED}, "
        f"missing {SUBSCRIBED - set(HOOKS['hooks'])}"
    )


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_no_agent_caps_its_own_loop(path):
    """`maxTurns` was added to every agent in commit f346998 (0.7.1 -> 0.9.0,
    2026-08-05) on the reasoning that an uncapped loop is an unbounded bill and
    a stuck process. It is not being re-added, and this test exists so a future
    session does not do so on that same general principle without seeing what
    happened when it shipped.

    **Removing it is a preference, not a proven fix, and the docstring that first
    shipped here claimed otherwise.** Recording what was actually measured,
    because the false version was more persuasive than the truth.

    The trigger was session KD-547, the same day the cap landed: four of eighteen
    subagent launches returned 255-256 bytes to the parent — an opening sentence,
    an `agentId` handle and a usage block — instead of the report the agent had
    written. Those reports are still in the subagents' own transcripts and two of
    them name real defects the parent never saw. The first account of this test
    said every stub had exceeded its cap and every under-cap run delivered, and
    inferred cause from it.

    That inference was wrong twice over. It compared `tool_uses` from the usage
    block against `maxTurns`, which counts assistant turns, not tool calls — two
    different numbers. Counting turns directly in the subagent transcripts kills
    it: the KD-547 stubs ran 55, 56 and 70 turns against caps of 30, 25 and 30,
    and a run in the session that reviewed this change went 84 turns against a
    cap of 30 and delivered an 11,713-character report. **An agent demonstrably
    runs well past `maxTurns` and finishes normally, so whatever ends these runs,
    the cap is not it.**

    The transcripts also show two different failures wearing one face. In KD-547
    the agents wrote full reports and the parent received a stub — a transit
    loss, recoverable from disk. In the later session the agents' own last
    message *is* the stub: they stopped without ever writing a report, and there
    is nothing to recover. Only the second kind is worth re-launching, and the
    delivery check in `skills/review/SKILL.md` step 4b handles both because it
    keys on the result being absent rather than on any theory of why.

    So this assertion encodes a decision — the user asked for the caps gone, and
    the ledger's measure of all agents at 6.3% of lifetime spend makes
    that cheap — and not a mechanism. Do not cite it as evidence that capping a
    loop causes lost reports. If a future session wants a bound, the honest
    reasons to still hesitate are that the caps never bound anything measurable
    here, and that a bound whose overrun is invisible is worse than none.
    """
    turns = frontmatter(path).get("maxTurns")

    assert turns is None, (
        f"{path.stem} sets maxTurns={turns}; the caps were removed by decision — "
        "read this test's docstring before re-adding one"
    )


def test_the_manifest_passes_the_official_validator():
    """`claude plugin validate --strict` checks the manifest against the real
    schema, which nothing in this file can do.

    Strict mode turns unrecognised-field warnings into errors, which is what
    catches a misspelled key — a field one character off is ignored at load
    time, so the feature it was meant to enable simply never happens.
    """
    try:
        proc = subprocess.run(
            ["claude", "plugin", "validate", str(PLUGIN), "--strict"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("the claude CLI is not on PATH here")

    assert proc.returncode == 0, proc.stdout + proc.stderr


# ------------------------------------------------ the hooks fire where they should


def test_a_hook_that_inspects_shell_commands_covers_both_shells():
    """`Bash` alone is a hook that never fires on some machines.

    On Windows without Git Bash the PowerShell tool is enabled automatically and
    Claude Code does not register the Bash tool at all, so shell commands arrive
    as `PowerShell` and a matcher of `Bash` matches nothing. `bash_watch` is what
    records files changed outside `Edit` and `Write`; missed, those edits never
    enter `files_touched`, and the scope fence and the end-of-turn gate both go on
    reporting clean over changes they never saw.
    """
    for event, groups in HOOKS["hooks"].items():
        for group in groups:
            matcher = group.get("matcher", "")
            if "Bash" not in matcher:
                continue
            scripts = [Path(h["args"][0]).name for h in group["hooks"] if h.get("args")]
            assert "PowerShell" in matcher, (
                f"{event} runs {scripts} on `{matcher}` — it will never fire where"
                " PowerShell is the shell tool"
            )


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_a_skill_may_only_instruct_tools_it_is_allowed(path):
    """A skill's `allowed-tools` is a hard restriction, not a hint.

    `review` told the model to apply a mutation with `Edit` and then confirm the
    test still passed — the step that turns "this test looks weak" into evidence.
    `Edit` was not in its `allowed-tools`, so the step could not run at all, and
    the skill's own instruction was the only thing saying otherwise.

    Backticked tool names only. Prose that happens to contain the word "write"
    is not an instruction, and a check that cannot tell the two apart gets its
    true sentences reworded until it passes.
    """
    text = path.read_text(encoding="utf-8")
    body = text.split("---", 2)[2]
    allowed = {t.strip() for t in str(frontmatter(path).get("allowed-tools", "")).split(",")}

    named = {m for m in re.findall(r"`(Edit|Write|Read|Grep|Glob|Bash|Task|Skill|TodoWrite|AskUserQuestion|WebFetch|WebSearch)`", body)}
    missing = sorted(named - allowed)

    assert not missing, (
        f"{path.parent.name} instructs {missing}, which its allowed-tools does not"
        " grant — the step cannot run and nothing says so"
    )
