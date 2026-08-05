"""Language servers: what is declared, and what gets said when one is absent.

Reading code is most of the work these agents do, and until now they did it with
grep — which finds a name but cannot tell a definition from a mention, cannot
follow a call through an interface, and cannot answer "who else uses this"
without reading every hit. `.lsp.json` is the fix.

Two things about it are load-bearing enough to be worth a test each: that
diagnostics stay off, because the default fights the per-edit check; and that a
missing binary is said once, at session start, rather than only in a `/plugin`
tab nobody opens.
"""

from __future__ import annotations

import json
from pathlib import Path

import lsp
from detect import get_profile

PLUGIN = Path(__file__).resolve().parent.parent
CONFIG = json.loads((PLUGIN / ".lsp.json").read_text(encoding="utf-8"))


def test_every_declared_server_has_the_two_required_fields():
    """A server missing `command` or `extensionToLanguage` is skipped at startup
    and the reason appears only under `claude --debug`. Silent, and the symptom
    is "navigation just doesn't work"."""
    for name, config in CONFIG.items():
        assert config.get("command"), f"{name} declares no command"
        assert config.get("extensionToLanguage"), f"{name} claims no extensions"


def test_diagnostics_are_off_on_every_server():
    """The default is `true`, which pushes every diagnostic for a file into the
    context after each edit — including errors that were already at HEAD.

    `post_edit_check.py` runs the same compilers and reports only what the edit
    caused, and the session banner tells the model in as many words not to go
    fixing problems it did not cause. An unfiltered second stream contradicts
    that instruction on every single edit, which is the failure that got this
    plugin's predecessor switched off.
    """
    for name, config in CONFIG.items():
        assert config.get("diagnostics") is False, (
            f"{name} leaves diagnostics on; it will re-report inherited breakage "
            f"that post_edit_check.py deliberately filters out"
        )


def test_no_extension_is_claimed_by_two_servers():
    """Only one server can own an extension — the first registered wins and the
    other never starts. Two of ours claiming `.ts` would make which one runs
    depend on load order, and nothing would report the loser."""
    owner: dict[str, str] = {}
    for name, config in CONFIG.items():
        for ext in config["extensionToLanguage"]:
            assert ext not in owner, f"{ext} claimed by both {owner[ext]} and {name}"
            owner[ext] = name


def test_the_crash_options_that_silently_skip_a_server_are_not_set():
    """`restartOnCrash` and `shutdownTimeout` need v2.1.205 or later. Before it,
    the schema accepted both and setting either caused the server to be skipped
    entirely — visible only under `--debug`.

    Neither is worth a version floor: the defaults are already what we want.
    """
    for name, config in CONFIG.items():
        for field in ("restartOnCrash", "shutdownTimeout"):
            assert field not in config, f"{name} sets {field} and is skipped pre-2.1.205"


def test_a_server_is_declared_for_every_language_the_checks_cover():
    """The plugin already knows how to compile, lint and test these four. A
    language it can check but not navigate is an asymmetry with no reason behind
    it — that repo's agents fall back to grep for no stated cause."""
    served = {lang for lang in lsp.SERVER_FOR_LANGUAGE}
    assert {"typescript", "javascript", "python", "go", "rust"} <= served

    for language, name in lsp.SERVER_FOR_LANGUAGE.items():
        assert name in CONFIG, f"{language} maps to {name}, which .lsp.json does not declare"


def test_every_declared_command_has_an_install_line():
    """"Install a language server" is not actionable and gets skipped. The whole
    value of the advisory is the exact command, so a server whose binary has no
    install line would produce advice the reader cannot act on."""
    for name, config in CONFIG.items():
        assert config["command"] in lsp.INSTALL, f"no install line for {name}"


def test_a_missing_server_names_the_language_and_the_install_command(monkeypatch):
    monkeypatch.setattr(lsp, "installed", lambda command: False)

    text = lsp.advisory({"languages": ["python"]})

    assert "python" in text
    assert "npm install -g pyright" in text


def test_nothing_is_said_when_the_server_is_installed(monkeypatch):
    """The common case, and the one that matters. This banner loads into every
    session, and the documented failure of always-on context is that a long one
    makes its own important lines get skimmed. There is nothing to do here, so
    there is nothing to say."""
    monkeypatch.setattr(lsp, "installed", lambda command: True)

    assert lsp.advisory({"languages": ["python", "typescript"]}) == ""


def test_only_this_repo_s_languages_are_mentioned(monkeypatch):
    """A Python developer does not need to be told about `gopls`. An advisory
    that lists four installs for a one-language repo is one the reader learns to
    skip, and then it is not there for the repo that needed it."""
    monkeypatch.setattr(lsp, "installed", lambda command: False)

    text = lsp.advisory({"languages": ["python"]})

    assert "gopls" not in text
    assert "rustup" not in text
    assert "typescript-language-server" not in text


def test_a_javascript_repo_is_served_by_the_typescript_server(monkeypatch):
    """`typescript-language-server` is what serves plain JavaScript, and
    `.lsp.json` claims `.js` for it. A separate entry would be a second server
    for the same extension, which means one of them never starts."""
    monkeypatch.setattr(lsp, "installed", lambda command: False)

    text = lsp.advisory({"languages": ["javascript"]})

    assert "typescript-language-server" in text


def test_one_install_line_per_server_when_a_repo_is_both(monkeypatch):
    """A repo detected as both TypeScript and JavaScript needs one server, so
    telling the user to install it twice reads as two separate problems."""
    monkeypatch.setattr(lsp, "installed", lambda command: False)

    text = lsp.advisory({"languages": ["typescript", "javascript"]})

    assert text.count("npm install -g typescript-language-server") == 1


def test_a_language_with_no_server_is_passed_over(monkeypatch):
    """`detect.py` reports `unknown` for a repo it cannot place, and `.lsp.json`
    has nothing for it. Passing over it must not be an error."""
    monkeypatch.setattr(lsp, "installed", lambda command: False)

    assert lsp.advisory({"languages": ["unknown"]}) == ""
    assert lsp.advisory({}) == ""


def test_an_unreadable_config_gives_no_advice_rather_than_an_exception(monkeypatch, tmp_path):
    """This is read on the way into a session banner. A plugin that fails to
    start a session because a file it only uses for advice went missing is worse
    than one that gives no advice."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))

    assert lsp.servers() == {}
    assert lsp.advisory({"languages": ["python"]}) == ""


def test_the_advisory_reaches_the_session_banner(monkeypatch, git_repo):
    """Asserted through `_describe` rather than on `lsp.advisory` directly.

    Testing the function proves the sentence can be produced; the question is
    whether the banner carries it, and a `session_start.py` that never calls it
    still passes the function-level assertion.
    """
    import session_start

    monkeypatch.setattr(lsp, "installed", lambda command: False)

    text = session_start._describe(get_profile(git_repo))

    assert "npm install -g pyright" in text, "the banner dropped the advisory"
