"""Loading domain knowledge by path instead of by frontmatter declaration.

The thing being replaced was not broken so much as advertised: nine lenses were
skills, every agent that might need one listed six in its frontmatter, and the
model then chose from its own frontmatter. Selection by path is code's job and
deterministic; selection by name is for the two agents whose subject *is* a
lens. Both have to keep working, and both fail the same silent way — the agent
carries on and reviews without the knowledge it was supposed to have.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

import lenses
from crew import registry

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
PAGES = Path(__file__).resolve().parent.parent / "references" / "lenses"
AGENTS = Path(__file__).resolve().parent.parent / "agents"


def _run(*args: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "lenses.py"), *args],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_a_path_loads_the_lens_for_its_domain():
    """`db/schema.ts` is a database file whoever is asking. This is the half
    that used to be a frontmatter list read by a model."""
    out = _run("db/schema.ts")

    assert "lens-database" in out
    assert "# Database lens" in out


def _loaded(out: str) -> set[str]:
    """The lenses actually emitted, read from the section markers.

    Not a substring search over the whole output: the lens bodies cross-refer to
    each other by name, so `"lens-frontend" not in out` fails on a page that
    merely mentions it. That is a test failing for a reason unrelated to what it
    is checking, which is how a correct assertion gets deleted as flaky.
    """
    return set(re.findall(r"^===== (\S+) =====$", out, re.MULTILINE))


def test_only_the_domains_the_paths_touch_are_loaded():
    """The gain over the frontmatter version, which loaded six lenses whatever
    the diff contained."""
    assert _loaded(_run("db/schema.ts")) == {"lens-database", "lens-typescript"}
    assert _loaded(_run("Dockerfile")) == {"lens-infra"}


def test_a_lens_named_outright_is_loaded_whatever_the_paths_say():
    """The security reviewer's subject is `lens-security` and its diff is
    usually a route handler, not anything under `auth/`. Matching on paths
    alone dropped the one lens it cannot work without — silently, because a
    reviewer with no lens still produces a review."""
    out = _run("lens-security", "app/api/checkout/route.ts")

    assert "# Security lens" in out
    assert "lens-backend" in out, "naming one lens must not suppress path matching"


def test_a_named_lens_is_not_loaded_twice():
    out = _run("lens-database", "db/schema.ts")

    assert out.count("===== lens-database =====") == 1


def test_no_arguments_prints_the_catalogue_rather_than_everything():
    """At plan time there are no paths yet. Printing all nine bodies there is
    how a context gets filled with eight irrelevant domains."""
    out = _run()

    assert "lens-database" in out and "lens-frontend" in out
    assert "# Database lens" not in out, "the catalogue printed the bodies"


def test_paths_matching_nothing_say_so_rather_than_failing():
    out = _run("README.txt")

    assert "No lens matches" in out


def test_a_missing_page_does_not_crash_the_agent_that_asked(monkeypatch, tmp_path):
    """This runs inside an agent doing something else. A stack trace there
    costs the whole task to save a page of advice; the wiring test is what
    catches the missing file, at the point somebody can fix it."""
    monkeypatch.setattr(lenses, "lens_path", lambda name: tmp_path / f"{name}.md")

    assert lenses.bodies(["lens-security"], ["db/schema.ts"]) == []


# An agent whose entire subject is one lens, and the lens it cannot work
# without. Path matching alone does not reach either: a security review is
# usually of a route handler, not of anything under `auth/`, and a test review
# is of the implementation as often as of the test file.
SUBJECT_LENS = {
    "reviewer-security": "lens-security",
    "reviewer-tests": "lens-testing",
    # Both run before any code exists, so there are usually no paths to match on
    # at all — and when there are, they are the implementation files, which
    # route to language lenses and never to this one. A request naming a spec
    # file is the rare case, not the normal one.
    "challenger": "lens-requirements",
    "designer": "lens-requirements",
}


@pytest.mark.parametrize("agent,lens", sorted(SUBJECT_LENS.items()))
def test_an_agent_whose_subject_is_a_lens_gets_it_unconditionally(agent, lens):
    """The regression path-matching introduced, and it is silent: a security
    reviewer with no security lens still produces a review, in the same shape
    and at the same length, having been told the classes it looks for are "in
    the lens you were given" when nothing was given.

    Asserted against the hook rather than the brief. The brief used to carry a
    `lenses.py` command with the lens named in it, which meant the guarantee
    depended on the agent choosing to run it. Now the SubagentStart hook selects
    and injects, so this is the file that has to be right.
    """
    import subagent_start

    named, _ = subagent_start.selection(agent, Path("/nonexistent"))
    assert lens in named, (
        f"{agent} would be given lenses by path only, so `{lens}` — its whole "
        f"subject — arrives only when the diff happens to touch a matching path"
    )


def test_the_pre_code_agents_are_not_routed_on_a_diff_that_does_not_exist_yet():
    """The challenger and the designer run before the code does. There is no
    diff to route on, so a path-based selection returns nothing at all — and an
    empty selection reads as "no domain knowledge applies", which is wrong
    rather than merely unhelpful."""
    for agent in subagent_start_module().PRE_CODE:
        named, files = subagent_start_module().selection(agent, Path("/nonexistent"))
        assert named, f"{agent} would start with no lens and no way to get one"
        assert files == [], f"{agent} is being routed on a diff that does not exist"


def subagent_start_module():
    import subagent_start

    return subagent_start


@pytest.mark.parametrize("scoped,bare", [
    ("harness:reviewer-perf", "reviewer-perf"),
    ("reviewer-perf", "reviewer-perf"),
    ("", ""),
])
def test_the_scoped_agent_name_is_resolved_to_the_bare_one(scoped, bare):
    """The event carries `harness:reviewer-perf`; every lookup in the module is
    keyed by the bare name. Getting this wrong makes every subject lens miss —
    silently, because the path-matched ones still arrive."""
    assert subagent_start_module().agent_name({"agent_type": scoped}) == bare


def test_every_page_says_which_domain_it_covers():
    """The header is what a reader uses to tell whether this is the lens they
    wanted, and it is all that survived of the skill frontmatter."""
    for lens in registry()["lenses"]:
        text = (PAGES / f"{lens['name']}.md").read_text(encoding="utf-8")
        assert f"Domain: {lens['domain']}" in text, lens["name"]
        assert text.lstrip().startswith(">"), f"{lens['name']} lost its summary"


def test_the_pages_kept_their_content():
    """The move was mechanical and the bodies are the whole value. A page that
    arrived empty would still route, still print a header, and teach nothing."""
    for lens in registry()["lenses"]:
        text = (PAGES / f"{lens['name']}.md").read_text(encoding="utf-8")
        assert len(text) > 1200, f"{lens['name']} is {len(text)} bytes — it lost its body"
        assert "## " in text


def test_the_catalogue_is_offered_even_when_lenses_did_load(hook_env, git_repo):
    """The shape this had was backwards. A total miss is rare and announces
    itself; the common failure is a *partial* miss — two lenses load, they look
    like the answer, and nothing tells the agent a third exists.

    Routing is a proxy however good the signals are, so the escape hatch has to
    be open when routing half-worked, not only when it failed outright.
    """
    import subagent_start

    out, _ = subagent_start.context_for("reviewer-security", git_repo)

    assert "<lens name=\"lens-security\">" in out, "the premise no longer holds"
    assert "lens-frontend" in out, "an unloaded lens is not offered"
    # Every offered lens carries the resolved path of its own page. The earlier
    # wording gave one template — `${CLAUDE_PLUGIN_ROOT}/references/lenses/<name>.md`
    # — and that placeholder is substituted into skill and agent content and into
    # hook commands, never into hook output. The agent was handed a variable no
    # shell of its own defines, and across every transcript on this machine not
    # one agent has ever opened a lens page.
    assert "${" not in out, "an unexpanded placeholder reached the agent"
    named = re.findall(r"(\S+)/<name>\.md", out)
    assert named, "the catalogue never says where the pages are"
    directory = Path(named[0])
    assert (directory / "lens-frontend.md").is_file(), (
        f"the catalogue points at {directory}, which does not hold the pages"
    )
