"""What the detector hands back, now that nothing is withheld.

Every check the detector finds is a check the harness runs. There is no approval
step and no second list: a command supplied by the repository is on the same
footing as one the plugin composed. These tests pin that, because the previous
behaviour was the opposite and its removal is easy to half-do — leaving a filter
in place that quietly empties `checks` is indistinguishable from a repo with no
tooling, which is exactly how the withholding went unnoticed for a day.
"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest

from detect import PROFILE_VERSION, get_profile


def _repo(tmp_path: Path, **files: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    for name, body in files.items():
        (root / name.replace("__", ".")).write_text(body, encoding="utf-8")
    return root


def test_a_repo_supplied_check_runs_without_approval(data_dir, tmp_path):
    """`.harness.json` `checks` is the repo naming a command outright.

    Nothing about this is composed by the plugin — the argv arrives with the
    clone. It is the case the removed trust boundary existed for, so it is the
    one that proves the boundary is gone.
    """
    root = _repo(
        tmp_path,
        __harness__json=json.dumps(
            {
                "checks": [
                    {
                        "kind": "test",
                        "argv": ["echo", "supplied-by-the-repo"],
                        "scope": "project",
                        "label": "repo check",
                        "blocking": True,
                        "extensions": [],
                    }
                ]
            }
        ),
    )

    profile = get_profile(root, refresh=True)

    labels = [c["label"] for c in profile["checks"]]
    assert "repo check" in labels, (
        "a command the repository supplied must be runnable with no approval step"
    )
    assert not profile.get("withheld_checks"), (
        "nothing is withheld any more, so the profile must not carry a second list"
    )


@pytest.mark.skipif(not shutil.which("pytest"), reason="needs a pytest console script on PATH")
def test_a_command_that_executes_tree_code_is_not_separated_out(data_dir, tmp_path):
    """`pytest` imports `conftest.py`, which is why it used to wait for trust.

    The plugin composed the argv, so this never read as "repo-authored" to
    anyone looking at it — and it is still the check most likely to be silently
    absent, because it is the one the end-of-turn gate depends on.

    Skipped rather than silently weakened where `pytest` is only reachable as
    `python -m pytest`: whether a `test` check exists at all is decided by
    `shutil.which`, so on such a runner this asserted nothing while looking
    green, and went red against correct code.
    """
    root = _repo(tmp_path, pytest__ini="[pytest]\n")

    profile = get_profile(root, refresh=True)

    assert any(c["kind"] == "test" for c in profile["checks"]), (
        "pytest must be present in checks, not held back in a separate list"
    )


def test_the_provenance_field_is_gone(data_dir, tmp_path):
    """Split from the test above so it does not inherit that one's skip.

    Nothing about the removed `source` field depends on which tools are
    installed, and this is the assertion that catches a half-removal.
    """
    root = _repo(tmp_path, pytest__ini="[pytest]\n")

    profile = get_profile(root, refresh=True)

    assert all("source" not in c for c in profile["checks"]), (
        "the provenance field existed only to feed the approval step; it should be gone"
    )


@pytest.mark.parametrize("kind", ["syntax", "test"])
def test_harness_json_can_disable_a_check_without_approval(data_dir, tmp_path, kind):
    """The documented escape hatch, which used to apply only to trusted repos.

    Both scopes are covered on purpose. `syntax` is file-scope and `test` is
    project-scope, and a filter restricted to file-scope checks — which would
    make `pytest` and `npm run test` undisableable — passed while only `syntax`
    was exercised.

    The precondition is asserted rather than assumed. A repo with nothing
    repo-authored counted as trusted by default, so without it this test went
    green against the unfixed code on any machine lacking a `pytest` binary.
    """
    root = _repo(
        tmp_path,
        pytest__ini="[pytest]\n",
        __harness__json=json.dumps({"disable": [kind]}),
    )

    baseline = get_profile(_repo(tmp_path / "baseline", pytest__ini="[pytest]\n"), refresh=True)
    assert any(c["kind"] == kind for c in baseline["checks"]), (
        f"precondition: an identical repo without the disable list must have a {kind} check,"
        " or this asserts nothing"
    )

    profile = get_profile(root, refresh=True)

    assert all(c["kind"] != kind for c in profile["checks"]), (
        "a repo's own `disable` list must take effect with no approval step"
    )
    assert profile.get("disabled_by_repo") == [kind], (
        "what the repo switched off has to stay visible, or session start blames"
        " a detection failure for the repo's own choice"
    )


def test_a_malformed_override_entry_is_dropped_rather_than_executed(data_dir, tmp_path):
    """A missing key used to be inert; now it reaches whoever subscripts it first.

    `guard()` catches the resulting `KeyError` and exits 0, so the hook does
    nothing and reports nothing — and `post_edit_check` never records
    `files_touched`, which switches the contract scope fence off as well.
    """
    root = _repo(
        tmp_path,
        __harness__json=json.dumps(
            {
                "checks": [
                    {"kind": "lint", "scope": "file", "label": "no argv"},
                    {"kind": "test", "argv": ["true"], "label": "no scope"},
                    {"argv": ["true"], "scope": "project", "label": "no kind"},
                    {"kind": "test", "argv": "not-a-list", "scope": "project"},
                    {"kind": "test", "argv": ["sh", {}], "scope": "project"},
                    {"kind": "test", "argv": ["true"], "scope": "project", "label": "good"},
                ]
            }
        ),
    )

    profile = get_profile(root, refresh=True)

    supplied = [c for c in profile["checks"] if c["label"] in ("no argv", "no scope", "no kind", "good")]
    assert [c["label"] for c in supplied] == ["good"], (
        "only the well-formed entry may survive"
    )
    assert all(
        {"kind", "argv", "scope", "label", "blocking", "extensions"} <= set(c)
        for c in profile["checks"]
    ), "every check reaching a consumer must carry the keys that consumer indexes"


def test_a_profile_cached_by_the_previous_version_is_rebuilt(data_dir, tmp_path):
    """The `PROFILE_VERSION` bump is what makes an in-place upgrade correct.

    A v8 cache carries `disabled_kinds` recorded-but-never-applied and `source`
    fields. Accepting one would silently ignore the repo's `disable` list until
    some manifest changed. Two mutations — reverting the constant, and deleting
    the version comparison — both left the whole suite green.
    """
    from state import profiles_dir, repo_key

    root = _repo(
        tmp_path,
        pytest__ini="[pytest]\n",
        __harness__json=json.dumps({"disable": ["test"]}),
    )
    fresh = get_profile(root, refresh=True)

    stale = {
        **fresh,
        "version": 8,
        "disabled_kinds": ["test"],
        "checks": [
            {
                "kind": "test",
                "argv": ["pytest", "-q"],
                "scope": "project",
                "label": "pytest",
                "blocking": True,
                "extensions": [],
                "source": "repo",
            }
        ],
    }
    (profiles_dir() / f"{repo_key(root)}.json").write_text(json.dumps(stale), encoding="utf-8")

    profile = get_profile(root)

    assert profile["version"] == PROFILE_VERSION, "a cache from the old version must be rebuilt"
    assert all(c["kind"] != "test" for c in profile["checks"]), (
        "the rebuild must apply the disable list the stale cache only recorded"
    )
    assert "disabled_kinds" not in profile


def test_a_vendored_tool_is_preferred_over_a_global_one(data_dir, tmp_path):
    """Deleting `test_trust.py` took detection's only coverage of this with it.

    Getting it wrong means checking a repo with the wrong tool version, which
    produces failures that are the harness's fault — the kind that gets a
    harness uninstalled.
    """
    root = _repo(tmp_path, pyproject__toml="[tool.ruff]\n")
    vendored = root / ".venv" / "bin"
    vendored.mkdir(parents=True)
    (vendored / "ruff").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (vendored / "ruff").chmod(0o755)

    profile = get_profile(root, refresh=True)

    ruff = [c for c in profile["checks"] if "ruff" in c["label"]]
    assert ruff, "a vendored ruff plus [tool.ruff] must be detected"
    assert all(c["argv"][0].startswith(str(root)) for c in ruff), (
        f"the vendored binary must win over any global one: {[c['argv'][0] for c in ruff]}"
    )


def test_no_script_still_reaches_for_the_removed_approval_step():
    """Deleting the behaviour and leaving a caller behind is a half-removal.

    `detect.py` imported `trust` lazily, inside a function, so a stale import
    would not fail until the hook ran in a real session rather than here.

    Parsed rather than grepped. A substring scan for `import trust` misses
    `from trust import repo_authored`, `import trust as t`, and
    `importlib.import_module("trust")` — verified: inserting the first of those
    left the whole suite green.
    """
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    assert not (scripts / "trust.py").exists(), "trust.py should have been deleted"

    importers = []
    for path in scripts.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n.split(".")[0] == "trust" for n in names):
                importers.append(path.name)
    assert not importers, f"scripts still importing the removed module: {importers}"

    stale = {
        path.name: word
        for path in scripts.glob("*.py")
        for word in ("withheld_checks", 'source": "repo"')
        if word in path.read_text(encoding="utf-8")
    }
    assert not stale, f"scripts still referencing the removed approval step: {stale}"
