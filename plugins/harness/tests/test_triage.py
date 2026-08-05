"""How much argument a request has earned, and where that is code's call to make.

The line this file guards is the one the module is built around: counting is
code's job, judging language is not. A test that let the classifier decide "make
it better" is vague would be pinning behaviour that must never exist, because a
regex confident enough to route on intent is confident enough to demand a design
debate over a typo — and that is how a process gets switched off.
"""

from __future__ import annotations

import subprocess

import roadmap
import triage


def _commit(root, name, text, message):
    (root / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=str(root), check=True, capture_output=True)


def test_a_file_rewritten_again_and_again_forces_the_argument(git_repo):
    """The plugin's own rule, applied before the work rather than after: a third
    or later patch to the same mechanism is evidence the design is wrong."""
    for i in range(triage.CHURN_ARGUES):
        _commit(git_repo, "a.py", f"value = {i}\n", f"patch {i}")

    ev = triage.classify("tidy up a.py", git_repo)

    assert ev.route == "B"
    assert ev.churn["a.py"] >= triage.CHURN_ARGUES
    assert "evidence the design is wrong" in " ".join(ev.because)


def test_a_quiet_file_forces_nothing(git_repo):
    """The other direction, and the one that keeps the flow usable. Ceremony on a
    small request is how a process gets bypassed."""
    ev = triage.classify("rename a variable in a.py", git_repo)

    assert ev.route == "undecided"
    assert ev.paths == ["a.py"]


def test_a_design_already_reworked_here_forces_the_full_debate(git_repo):
    """The strongest evidence there is: this shape was tried and did not hold."""
    entry = roadmap.append(git_repo, "cache the thing", "- decided: a.py grows a cache")
    roadmap.set_outcome(git_repo, entry.id, "reworked")

    ev = triage.classify("add caching to a.py", git_repo)

    assert ev.route == "C"
    assert entry.id in ev.reworked[0]
    assert "already tried here" in " ".join(ev.because)


def test_a_related_decision_that_held_is_reported_but_forces_nothing(git_repo):
    """Past work on the same file is context, not an argument. Treating every
    prior decision as an objection would route everything to C."""
    roadmap.append(git_repo, "a.py got its shape", "- decided: a.py holds the value")

    ev = triage.classify("change the value in a.py", git_repo)

    assert ev.related and not ev.reworked
    assert ev.route == "undecided"


def test_language_is_never_classified_by_this_module(git_repo):
    """The load-bearing constraint. Whether a goal is clear is a judgement the
    model makes; if this ever starts answering it, the regex will be confidently
    wrong in both directions."""
    vague = triage.classify("make the whole thing better and faster somehow", git_repo)
    precise = triage.classify("make test_state.py::test_shard_merge pass", git_repo)

    assert vague.route == "undecided"
    assert precise.route == "undecided"
    # And the rule travels with the readings, because a reading with no decision
    # rule attached measurably fails to change behaviour.
    assert "does anyone know what done looks like" in triage.render(vague)


def test_a_partial_path_still_resolves(git_repo):
    """People name `scripts/stop_gate.py` for a file that lives three directories
    deeper. Refusing to look it up reports no churn for the file the request is
    entirely about."""
    (git_repo / "deep" / "nested").mkdir(parents=True)
    _commit(git_repo, "deep/nested/thing.py", "x = 1\n", "add nested")

    ev = triage.classify("fix nested/thing.py", git_repo)

    assert ev.paths == ["deep/nested/thing.py"]


def test_a_name_matching_several_files_is_claimed_for_none(git_repo):
    """Two matches and there is no way to tell which was meant. Guessing invents
    churn evidence for a file the request never mentioned."""
    (git_repo / "one").mkdir()
    (git_repo / "two").mkdir()
    _commit(git_repo, "one/shared.py", "x = 1\n", "one")
    _commit(git_repo, "two/shared.py", "x = 2\n", "two")

    assert triage.classify("fix shared.py", git_repo).paths == []


def test_a_word_that_is_not_a_file_is_not_probed(git_repo):
    """`plan` and `state` are English before they are files."""
    assert triage.classify("improve the plan and the state it keeps", git_repo).paths == []


def test_a_repo_that_cannot_test_itself_says_so(git_repo, tmp_path):
    """Table 5-1's second axis. Nothing automated can prove done here, so the
    goal is manually verified whatever the request says."""
    assert triage.classify("anything", git_repo).can_self_check is True

    bare = tmp_path / "bare"
    bare.mkdir()
    ev = triage.classify("anything", bare)
    assert ev.can_self_check is False
    assert "verify itself" in " ".join(ev.because)


def test_classifying_outside_a_repo_is_not_an_error(tmp_path):
    ev = triage.classify("do a thing", tmp_path)

    assert ev.route == "undecided"
    assert ev.paths == []


def test_a_repo_with_no_history_says_so_rather_than_reporting_calm(tmp_path):
    """Outside a git repository every `git log` returns nothing, so every churn
    count is zero — and the summary said "nothing in the history argues about this
    request by itself".

    That reads as a reading: the files are stable, so build it. What actually
    happened is that the strongest evidence this module has was never available.
    Reporting an absent measurement as a calm one is how triage talks a request
    into route A that it has no grounds to place anywhere.
    """
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    ev = triage.classify("change app.py", tmp_path)

    assert ev.has_history is False
    assert any("not a git repository" in b for b in ev.because), ev.because
    assert not any("nothing in the history argues" in b for b in ev.because), ev.because


def test_a_git_repo_with_a_calm_file_still_reports_it_as_calm(tmp_path, git_repo):
    """The other direction. If the branch above fired inside a real repository it
    would suppress the genuine finding — that the history was consulted and had
    nothing to say — which is the reading route A depends on."""
    ev = triage.classify("change a.py", git_repo)

    assert ev.has_history is True
    assert any("nothing in the history argues" in b for b in ev.because), ev.because


def test_the_json_flag_works_alongside_a_request(git_repo, capsys, monkeypatch):
    """`request == "--json"` compared the flag against the *whole* argument string,
    so the flag only did anything when it was the only argument.

    `triage.py --json "add retries to the client"` therefore printed the human
    rendering, and fed the literal `--json` into the path scan as well. Anything
    parsing that output got neither JSON nor any indication it had not.
    """
    import json as json_mod

    monkeypatch.setattr(triage.sys, "argv", ["triage.py", "--json", "change a.py"])
    monkeypatch.chdir(git_repo)  # `main()` resolves the root from the cwd

    triage.main()

    payload = json_mod.loads(capsys.readouterr().out)
    assert "route" in payload and "churn" in payload, payload
    assert "--json" not in payload["paths"], "the flag was scanned as if it were a path"
