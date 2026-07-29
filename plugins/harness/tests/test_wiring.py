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


def test_every_lens_in_the_registry_is_a_real_skill():
    for lens in REGISTRY["lenses"]:
        assert (SKILLS / lens["name"] / "SKILL.md").is_file(), lens["name"]


def test_the_registry_and_the_lens_frontmatter_agree_on_paths():
    """Two declarations of one fact, maintained by hand. They drift silently,
    and then the crew line shown to the user disagrees with what is loaded."""
    for lens in REGISTRY["lenses"]:
        declared = frontmatter(SKILLS / lens["name"] / "SKILL.md").get("paths", [])
        assert set(declared) == set(lens["paths"]), (
            f"{lens['name']}: skill-only {set(declared) - set(lens['paths'])}, "
            f"registry-only {set(lens['paths']) - set(declared)}"
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


_STATE_DIRS = ("data_dir", "ledger_dir", "contracts_dir", "profiles_dir", "shards_dir")
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
        if isinstance(node, ast.Name) and node.id in _STATE_DIRS:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _STATE_DIRS:
            return True
        if isinstance(node, ast.alias) and node.name in _STATE_DIRS:
            return True
    return False


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_a_skill_that_shells_out_to_plugin_state_passes_the_data_directory(path):
    """A shell does not inherit `CLAUDE_PLUGIN_DATA`; the hooks are given it.

    So a skill command that reads or writes plugin state resolves a *different*
    directory than every hook, and reports confident success against it.
    `/harness:report` answered "No sessions recorded yet" over a ledger holding
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


def test_changing_plugin_code_without_bumping_the_version_is_caught_here():
    """An unbumped version makes a published fix a silent no-op.

    `claude plugin marketplace update` refreshes marketplace metadata but does
    not re-fetch a plugin whose version string it has already seen. It finds the
    version directory present, leaves it, and prints "Successfully updated" —
    while the running hooks go on executing the previous code. The roadmap
    records a full publish cycle lost to this, rediscovered only by listing the
    cache directory by hand.

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
