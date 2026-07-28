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

import json
import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
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


def test_write_capable_agents_are_named_here_on_purpose():
    """Only `worker` may edit. A second write-capable agent is a decision, not
    an accident, and the parallel-edit safety was designed around exactly one."""
    writers = {
        p.stem
        for p in AGENT_FILES
        if {"Write", "Edit"} & {t.strip() for t in str(frontmatter(p).get("tools", "")).split(",")}
    }
    assert writers == {"worker"}, f"unexpected write-capable agents: {writers - {'worker'}}"
